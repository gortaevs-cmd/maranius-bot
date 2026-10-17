"""Погода, геолокация, кэш и лунный блок; без зависимости от точки запуска бота."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Dict, Optional

import ephem
import httpx
import pytz
from timezonefinder import TimezoneFinder
from telegram import Update
from telegram.ext import ContextTypes

import ui
from integrations.diagnostics import log_error

WEATHER_CITY_QUERY = "Moscow"

WEATHER_CITY_NAME = "Москва"

WEATHER_CODES = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "облачно",
    45: "туман",
    48: "изморозь",
    51: "морось",
    53: "морось",
    55: "морось",
    61: "дождь",
    63: "дождь",
    65: "сильный дождь",
    71: "снег",
    73: "снег",
    75: "сильный снег",
    77: "снежные зёрна",
    80: "ливень",
    81: "ливень",
    82: "сильный ливень",
    85: "снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "гроза с сильным градом",
}

NOMINATIM_HEADERS = {"User-Agent": "MaraniusBot/1.0 (aif5.ru)"}

WEATHER_LOCATION_TTL_HOURS = 4


def _get_timezone_by_coords(lat: float, lon: float) -> Optional[str]:
    """Определить часовой пояс по координатам."""
    try:
        tf = TimezoneFinder()
        timezone_str = tf.timezone_at(lat=lat, lng=lon)
        return timezone_str
    except Exception:
        return None


def _format_local_time(utc_time: datetime, timezone_str: Optional[str] = None) -> str:
    """Форматировать UTC время в локальное время пользователя."""
    if timezone_str:
        try:
            tz = pytz.timezone(timezone_str)
            local_time = utc_time.replace(tzinfo=pytz.UTC).astimezone(tz)
            return local_time.strftime("%d.%m.%Y %H:%M")
        except Exception:
            pass
    # Если часовой пояс не определен, используем UTC
    return utc_time.strftime("%d.%m.%Y %H:%M (UTC)")


def get_weather_emoji(code: int, temp: Optional[float] = None) -> str:
    """Получить смайлик для погодных условий."""
    if temp is not None:
        if temp < -10:
            return "🥶"  # Очень холодно
        elif temp < 0:
            return "❄️"  # Мороз
        elif temp < 10:
            return "🧊"  # Холодно
        elif temp < 20:
            return "🌤️"  # Прохладно
        elif temp < 30:
            return "☀️"  # Тепло
        else:
            return "🔥"  # Жарко

    # Смайлики по погодным условиям
    emoji_map = {
        0: "☀️",   # ясно
        1: "🌤️",   # преимущественно ясно
        2: "⛅",   # переменная облачность
        3: "☁️",   # облачно
        45: "🌫️",  # туман
        48: "🌫️",  # изморозь
        51: "🌦️",  # морось
        53: "🌦️",  # морось
        55: "🌦️",  # морось
        61: "🌧️",  # дождь
        63: "🌧️",  # дождь
        65: "⛈️",  # сильный дождь
        71: "❄️",  # снег
        73: "❄️",  # снег
        75: "🌨️",  # сильный снег
        77: "❄️",  # снежные зёрна
        80: "🌧️",  # ливень
        81: "🌧️",  # ливень
        82: "⛈️",  # сильный ливень
        85: "🌨️",  # снегопад
        86: "🌨️",  # сильный снегопад
        95: "⛈️",  # гроза
        96: "⛈️",  # гроза с градом
        99: "⛈️",  # гроза с сильным градом
    }
    return emoji_map.get(code, "🌤️")


def get_wind_direction(degrees: Optional[float]) -> str:
    """Преобразовать направление ветра из градусов в текст."""
    if degrees is None:
        return ""
    directions = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
    index = int((degrees + 22.5) / 45) % 8
    return directions[index]


def _get_moon_data(d: date) -> Dict[str, Any]:
    """Получить данные о луне через ephem (фазы, освещённость, даты)."""
    observer = ephem.Observer()
    observer.date = d

    prev_new_moon = ephem.previous_new_moon(observer.date)
    next_new_moon = ephem.next_new_moon(observer.date)
    # Полнолуние текущего цикла = первое полнолуние после предыдущего новолуния
    full_moon_this_cycle = ephem.next_full_moon(prev_new_moon)

    prev_new = ephem.Date(prev_new_moon).datetime().date()
    next_new = ephem.Date(next_new_moon).datetime().date()
    full_this = ephem.Date(full_moon_this_cycle).datetime().date()

    moon = ephem.Moon()
    moon.compute(observer)
    illumination = moon.moon_phase  # 0 = новолуние, 1 = полнолуние

    return {
        "prev_new_moon": prev_new,
        "next_new_moon": next_new,
        "full_moon_this_cycle": full_this,
        "illumination": illumination,
    }


def _moon_phase_name(d: date) -> str:
    """Фаза луны по ephem (освещённость и даты фаз)."""
    data = _get_moon_data(d)
    return _moon_phase_from_data(d, data)


def _moon_phase_from_data(d: date, data: Dict[str, Any]) -> str:
    """Фаза луны из готовых данных."""
    ill = data["illumination"]
    full_this = data["full_moon_this_cycle"]
    waxing = d < full_this

    if ill < 0.03:
        return "новолуние"
    if ill >= 0.97:
        return "полнолуние"
    if 0.45 <= ill <= 0.55:
        return "первая четверть" if waxing else "последняя четверть"
    return "растущая луна" if waxing else "убывающая луна"


def get_lunar_day(d: date) -> int:
    """Рассчитать лунные сутки (от 1 до 29-30) от предыдущего новолуния."""
    data = _get_moon_data(d)
    return _lunar_day_from_data(d, data)


def _lunar_day_from_data(d: date, data: Dict[str, Any]) -> int:
    """Лунные сутки из готовых данных."""
    prev_new = data["prev_new_moon"]
    delta = (d - prev_new).days
    return max(1, min(30, delta + 1))


def get_moon_phases_dates(d: date) -> Dict[str, Any]:
    """Получить даты фаз текущего лунного цикла."""
    return _get_moon_data(d)


def get_moon_emoji(phase_name: str) -> str:
    """Получить смайлик для фазы луны."""
    emoji_map = {
        "новолуние": "🌑",
        "растущая луна": "🌒",
        "первая четверть": "🌓",
        "полнолуние": "🌕",
        "убывающая луна": "🌖",
        "последняя четверть": "🌗",
    }
    return emoji_map.get(phase_name, "🌙")


def _format_moon_text() -> str:
    """Текст блока «Луна сегодня»."""
    today = date.today()
    phases = _get_moon_data(today)
    phase_name = _moon_phase_from_data(today, phases)
    lunar_day = _lunar_day_from_data(today, phases)

    emoji = get_moon_emoji(phase_name)
    ill_pct = int(round(phases["illumination"] * 100))

    prev_new = phases["prev_new_moon"]
    next_new = phases["next_new_moon"]
    full_this = phases["full_moon_this_cycle"]

    days_to_new = (next_new - today).days

    obs = ephem.Observer()
    obs.date = today
    next_full_date = ephem.Date(ephem.next_full_moon(obs.date)).datetime().date()
    days_to_full = (next_full_date - today).days

    if days_to_full == 0:
        obs.date = next_new
        next_full_date = ephem.Date(ephem.next_full_moon(obs.date)).datetime().date()
        days_to_full = (next_full_date - today).days

    parts = [
        f"<b>{emoji} Луна сегодня ({today.strftime('%d.%m.%Y')}):</b>",
        f"Фаза: {phase_name.capitalize()}",
        f"Лунные сутки: {lunar_day}",
        f"Освещённость: {ill_pct}%",
        "",
        "Текущий цикл:",
        f"  🌑 Новолуние: {prev_new.strftime('%d.%m.%Y')}",
        f"  🌒 Растущая луна: {prev_new.strftime('%d.%m.%Y')} — {full_this.strftime('%d.%m.%Y')}",
        f"  🌕 Полнолуние: {full_this.strftime('%d.%m.%Y')}",
        f"  🌖 Убывающая луна: {full_this.strftime('%d.%m.%Y')} — {next_new.strftime('%d.%m.%Y')}",
        f"  🌑 Новолуние: {next_new.strftime('%d.%m.%Y')}",
        "",
    ]

    if days_to_new > 0:
        parts.append(f"Следующее новолуние через {days_to_new} дн.")
    if days_to_full > 0:
        parts.append(f"Следующее полнолуние через {days_to_full} дн.")
    return "\n".join(parts)


async def _get_city_from_coords(lat: float, lon: float) -> str:
    """Определение города по координатам (Nominatim)."""
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json&addressdetails=1"
    )
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, headers=NOMINATIM_HEADERS)
            response.raise_for_status()
            data = response.json()
        addr = (data.get("address") or {})
        # Более агрессивный поиск названия места
        city_name = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
            or addr.get("suburb")
            or addr.get("city_district")
            or addr.get("neighbourhood")
            or addr.get("county")
            or addr.get("state")
            or addr.get("region")
        )
        # Если не нашли, пробуем взять первую часть display_name
        if not city_name:
            display_name = data.get("display_name", "")
            if display_name:
                city_name = display_name.split(",")[0].strip()
        return city_name or "Локация"
    except Exception:
        return "Локация"


def _location_age_hours(updated_at: str) -> Optional[float]:
    try:
        last_update = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
        return (datetime.utcnow() - last_update).total_seconds() / 3600
    except Exception:
        return None


def _location_is_fresh(user_data: Dict[str, Any]) -> bool:
    loc = user_data.get("last_location") or {}
    updated_at = loc.get("updated_at")
    if not updated_at:
        return False
    hours = _location_age_hours(updated_at)
    return hours is not None and 0 <= hours <= WEATHER_LOCATION_TTL_HOURS


def _weather_cache_is_fresh(user_data: Dict[str, Any]) -> bool:
    location = user_data.get("last_location")
    if not location or user_data.get("last_weather_location") != location:
        return False
    hours = _location_age_hours(user_data.get("last_weather_fetched_at", ""))
    return bool(user_data.get("last_weather_text")) and (
        hours is not None and 0 <= hours <= WEATHER_LOCATION_TTL_HOURS
    )


def _format_time_iso(iso_str: str) -> str:
    """Из ISO времени оставить только HH:MM."""
    if not iso_str or "T" not in iso_str:
        return iso_str or "—"
    return iso_str.split("T")[1][:5]


async def _weather_at_coords(
    lat: float,
    lon: float,
    place_name: str,
    updated_at: Optional[str] = None,
    timezone_str: Optional[str] = None,
    *,
    fetched_at: Optional[datetime] = None,
) -> Optional[str]:
    """Запрос погоды через Open-Meteo: сейчас + давление, влажность, восход/закат, луна, завтра."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&timezone=auto"
        "&current_weather=true"
        "&hourly=relative_humidity_2m,surface_pressure,apparent_temperature"
        "&daily=sunrise,sunset,temperature_2m_max,temperature_2m_min,weathercode,precipitation_probability_max"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return None

    cw = data.get("current_weather") or {}
    temp = cw.get("temperature")
    if temp is None:
        temp = "—"
    code = cw.get("weathercode", 0)
    wind = cw.get("windspeed")
    wind_direction_deg = cw.get("winddirection")
    desc = WEATHER_CODES.get(int(code), "без осадков")

    hourly = data.get("hourly") or {}
    h_times = hourly.get("time") or []
    humidity, pressure, apparent_temp = None, None, None
    if h_times and cw.get("time"):
        try:
            cur = str(cw["time"])[:13]
            for i, t in enumerate(h_times):
                if str(t)[:13] == cur:
                    if "relative_humidity_2m" in hourly:
                        humidity = (hourly["relative_humidity_2m"] or [None])[i]
                    if "surface_pressure" in hourly:
                        pressure = (hourly["surface_pressure"] or [None])[i]
                    if "apparent_temperature" in hourly:
                        apparent_temp = (hourly["apparent_temperature"] or [None])[i]
                    break
        except (IndexError, KeyError, TypeError):
            pass
    if humidity is None and (hourly.get("relative_humidity_2m")):
        humidity = (hourly["relative_humidity_2m"] or [None])[0]
    if pressure is None and (hourly.get("surface_pressure")):
        pressure = (hourly["surface_pressure"] or [None])[0]
    if apparent_temp is None and (hourly.get("apparent_temperature")):
        apparent_temp = (hourly["apparent_temperature"] or [None])[0]

    daily = data.get("daily") or {}
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []
    sunrise_str = _format_time_iso(sunrises[0]) if sunrises else "—"
    sunset_str = _format_time_iso(sunsets[0]) if sunsets else "—"

    today_date = date.today()
    moon_str = _moon_phase_name(today_date)

    # Получаем температуру вчера для сравнения
    d_times = daily.get("time") or []
    d_max = daily.get("temperature_2m_max") or []
    d_min = daily.get("temperature_2m_min") or []
    temp_change = None
    if len(d_times) >= 1 and len(d_max) >= 1:
        yesterday_max = d_max[0] if len(d_max) > 0 else None
        if yesterday_max is not None and isinstance(temp, (int, float)):
            diff = temp - yesterday_max
            if abs(diff) >= 1:  # Показываем только если разница >= 1 градус
                temp_change = diff

    # Получаем смайлики
    temp_emoji = get_weather_emoji(int(code), temp if isinstance(temp, (int, float)) else None)
    weather_emoji = get_weather_emoji(int(code))

    # Формируем строку температуры с изменением
    temp_str = f"{temp_emoji} {temp} °C"
    if temp_change is not None:
        if temp_change > 0:
            temp_str += f" ↗️ (+{temp_change:.1f}°)"
        else:
            temp_str += f" ↘️ ({temp_change:.1f}°)"

    parts = [
        f"<b>{weather_emoji} Погода{' ' + place_name if not place_name.startswith('(') else ''}:</b>",
        f"  {desc.capitalize()}, {temp_str}",
    ]

    # Ощущается как
    if apparent_temp is not None and isinstance(temp, (int, float)) and abs(apparent_temp - temp) >= 1:
        parts.append(f"  🌡️ Ощущается как: {apparent_temp:.1f} °C")

    # Ветер с направлением
    if wind is not None:
        wind_dir = get_wind_direction(wind_direction_deg)
        wind_str = f"💨 Ветер: {wind} км/ч"
        if wind_dir:
            wind_str += f" ({wind_dir})"
        parts.append(f"  {wind_str}")

    if humidity is not None:
        parts.append(f"  💧 Влажность: {humidity}%")
    if pressure is not None:
        parts.append(f"  📊 Давление: {pressure} гПа")
    parts.append(f"  🌅 Восход: {sunrise_str}  ·  🌇 Закат: {sunset_str}")
    parts.append(f"  🌙 Луна: {moon_str}")

    # Прогноз на завтра
    d_code = daily.get("weathercode") or []
    d_precip_prob = daily.get("precipitation_probability_max") or []
    if len(d_times) >= 2 and len(d_max) >= 2 and len(d_min) >= 2:
        t_max = d_max[1]
        t_min = d_min[1]
        code_tom = d_code[1] if len(d_code) >= 2 else 0
        desc_tom = WEATHER_CODES.get(int(code_tom), "без осадков")
        weather_emoji_tom = get_weather_emoji(int(code_tom))
        parts.append("")
        parts.append(f"{weather_emoji_tom} Завтра:")
        parts.append(f"  {desc_tom.capitalize()}, от {t_min} до {t_max} °C")

        # Вероятность осадков для завтра
        if len(d_precip_prob) >= 2 and d_precip_prob[1] is not None and d_precip_prob[1] > 0:
            parts.append(f"  🌧️ Вероятность осадков: {d_precip_prob[1]}%")

    # Время обновления в самом низу
    stamp = fetched_at
    if stamp is None and updated_at:
        try:
            stamp = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            stamp = None
    if stamp is None:
        stamp = datetime.utcnow()
    parts.append("")
    parts.append(f"Обновлено: {_format_local_time(stamp, timezone_str)}")

    return "\n".join(parts)


async def _weather_ask_location(message) -> None:
    await message.reply_text(
        ui.MSG_WEATHER_ASK_LOCATION,
        parse_mode="HTML",
        reply_markup=ui.get_weather_share_keyboard(),
    )


async def _weather_ask_location_expired(message) -> None:
    await message.reply_text(
        ui.MSG_WEATHER_LOCATION_EXPIRED,
        parse_mode="HTML",
        reply_markup=ui.get_weather_share_keyboard(),
    )


@dataclass
class WeatherHandlers:
    users_lock: Any
    load_users: Callable[[], Dict[str, Any]]
    save_users: Callable[[Dict[str, Any]], None]
    require_access: Callable[[Update, Any, str], Awaitable[bool]]
    ensure_user_saved: Callable[..., Awaitable[bool]]
    main_keyboard_for: Callable[[int], Any]
    edit_or_reply: Callable[..., Awaitable[None]]

    async def _save_user_location(self, uid: str, lat: float, lon: float) -> Dict[str, Any]:
        now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        timezone_str = _get_timezone_by_coords(lat, lon)
        async with self.users_lock:
            users = self.load_users()
            row = users.setdefault(uid, {})
            row["last_location"] = {"lat": lat, "lon": lon, "updated_at": now_str}
            for key in ("last_weather_text", "last_weather_location", "last_weather_fetched_at"):
                row.pop(key, None)
            if timezone_str:
                row["timezone"] = timezone_str
            else:
                row.pop("timezone", None)
            self.save_users(users)
            return dict(row)

    async def _save_weather_cache(self, uid: str, text: str, *, location: Dict[str, Any]) -> bool:
        """Не сохранять результат запроса, если за это время изменилось место."""
        async with self.users_lock:
            users = self.load_users()
            row = users.get(uid)
            if not row or row.get("last_location") != location:
                return False
            row["last_weather_text"] = text
            row["last_weather_location"] = dict(location)
            row["last_weather_fetched_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            self.save_users(users)
            return True

    async def _weather_fetch_and_send(self, update: Update, user_data: Dict[str, Any]) -> None:
        """Запрос API по сохранённым coords, если кэша текста ещё нет."""
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return
        uid = str(user.id)
        loc = user_data.get("last_location") or {}
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            await _weather_ask_location(message)
            return
        place_name = await _get_city_from_coords(lat, lon)
        if not place_name or place_name == "Локация":
            place_name = f"({lat:.4f}, {lon:.4f})"
        text = await _weather_at_coords(
            lat,
            lon,
            place_name,
            loc.get("updated_at"),
            user_data.get("timezone"),
            fetched_at=datetime.utcnow(),
        )
        main_kb = self.main_keyboard_for(user.id)
        if not text:
            await message.reply_text(ui.MSG_WEATHER_FETCH_FAIL, reply_markup=main_kb)
            return
        if not await self._save_weather_cache(uid, text, location=loc):
            await message.reply_text(ui.MSG_WEATHER_LOCATION_CHANGED, reply_markup=main_kb)
            return
        await message.reply_text(text, parse_mode="HTML", reply_markup=main_kb)

    async def weather(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Ещё → Погода: всегда новое сообщение, прогноз без inline-кнопок."""
        if not await self.require_access(update, context, "weather"):
            return
        await self.ensure_user_saved(update, bot=context.bot)
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return

        uid = str(user.id)
        user_data = self.load_users().get(uid, {})
        main_kb = self.main_keyboard_for(user.id)

        if "last_location" not in user_data:
            await _weather_ask_location(message)
            return

        if not _location_is_fresh(user_data):
            await _weather_ask_location_expired(message)
            return

        cached = user_data.get("last_weather_text")
        if _weather_cache_is_fresh(user_data):
            hint = ui.MSG_WEATHER_CACHE_HINT.strip()
            display = cached if hint in cached else cached + ui.MSG_WEATHER_CACHE_HINT
            await message.reply_text(display, parse_mode="HTML", reply_markup=main_kb)
            return

        await self._weather_fetch_and_send(update, user_data)

    async def weather_ask_new_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Смена места до истечения 4 ч."""
        message = update.effective_message
        if not message:
            return
        await _weather_ask_location(message)

    async def weather_by_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.location:
            return

        if not await self.require_access(update, context, "weather"):
            return
        await self.ensure_user_saved(update, bot=context.bot)

        user = update.effective_user
        if not user:
            return

        uid = str(user.id)
        main_kb = self.main_keyboard_for(user.id)

        try:
            lat = update.message.location.latitude
            lon = update.message.location.longitude
            user_data = await self._save_user_location(uid, lat, lon)
            place_name = await _get_city_from_coords(lat, lon)
            if not place_name or place_name == "Локация":
                place_name = f"({lat:.4f}, {lon:.4f})"

            text = await _weather_at_coords(
                lat,
                lon,
                place_name,
                user_data.get("last_location", {}).get("updated_at"),
                user_data.get("timezone"),
                fetched_at=datetime.utcnow(),
            )
            if not text:
                await update.message.reply_text(
                    ui.MSG_WEATHER_GEO_FAIL,
                    reply_markup=main_kb,
                )
                return

            if not await self._save_weather_cache(uid, text, location=user_data["last_location"]):
                await update.message.reply_text(
                    ui.MSG_WEATHER_LOCATION_CHANGED, reply_markup=main_kb
                )
                return
            await update.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=main_kb,
            )
        except Exception as e:
            log_error("weather_location_failed", e)
            await update.message.reply_text(
                ui.MSG_WEATHER_GEO_FAIL,
                reply_markup=main_kb,
            )

    async def moon_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /moon или inline «Ещё → Луна»."""
        if not await self.require_access(update, context, "moon"):
            return
        await self.ensure_user_saved(update, bot=context.bot)
        message = update.effective_message
        if not message:
            return
        text = _format_moon_text()
        if update.callback_query:
            await self.edit_or_reply(message, text, ui.get_back_to_more_keyboard())
            return
        await message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=self.main_keyboard_for(update.effective_user.id)
            if update.effective_user
            else ui.get_main_keyboard(),
        )
