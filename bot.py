import asyncio
import json
import os
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Set

import httpx
import pytz
import ephem
from timezonefinder import TimezoneFinder
from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardRemove,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    MessageReactionHandler,
    filters,
)

# Импорты интеграций
from integrations.zenclass_handlers import (
    zenclass_test,
    zenclass_students,
    zenclass_courses,
    zenclass_create_student_handler,
    zenclass_create_student_with_email,
    is_valid_email,
    get_zenclass_menu_keyboard,
)
from integrations import platform_db
from events.handlers import (
    make_subscribe_handler,
    make_unsubscribe_handler,
    make_reaction_handler,
)
from events import storage as events_storage


load_dotenv()


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CODE = os.getenv("ADMIN_CODE", "admin123")  # Код доступа по умолчанию

# Файлы с данными
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")
ADMINS_FILE = os.path.join(BASE_DIR, "admins.json")
_users_lock = asyncio.Lock()
_admins_lock = asyncio.Lock()

# Множество chat_id для отслеживания групп
_known_chats: Set[int] = set()


def _load_users() -> Dict[str, Any]:
    """Загрузить словарь user_id -> данные из users.json."""
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_users(users: Dict[str, Any]) -> None:
    """Сохранить словарь пользователей в users.json."""
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _load_admins() -> Set[int]:
    """Загрузить множество admin user_id из admins.json."""
    if not os.path.exists(ADMINS_FILE):
        return set()
    try:
        with open(ADMINS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("admins", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_admins(admins: Set[int]) -> None:
    """Сохранить множество администраторов в admins.json."""
    with open(ADMINS_FILE, "w", encoding="utf-8") as f:
        json.dump({"admins": list(admins)}, f, ensure_ascii=False, indent=2)


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


async def ensure_user_saved(update: Update) -> None:
    """Обновить/добавить данные пользователя из update и сохранить в users.json."""
    user = update.effective_user
    if not user:
        return
    uid = str(user.id)
    now = datetime.utcnow()
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name or "",
        "language_code": user.language_code or "",
        "is_premium": getattr(user, "is_premium", False),
        "last_seen": now_str,
    }
    
    # Сохранение локации, если она есть в сообщении
    if update.message and update.message.location:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        timezone_str = _get_timezone_by_coords(lat, lon)
        record["last_location"] = {
            "lat": lat,
            "lon": lon,
            "updated_at": now_str,
        }
        if timezone_str:
            record["timezone"] = timezone_str
    
    async with _users_lock:
        users = _load_users()
        if uid not in users:
            record["first_seen"] = now_str
        else:
            record["first_seen"] = users[uid].get("first_seen", now_str)
            # Сохраняем существующую локацию и часовой пояс, если они есть
            if "last_location" not in record and "last_location" in users[uid]:
                record["last_location"] = users[uid]["last_location"]
            if "timezone" not in record and "timezone" in users[uid]:
                record["timezone"] = users[uid]["timezone"]
        users[uid] = record
        _save_users(users)

    # Кросс-сервисная база: создаём/обновляем platform_user по telegram_id
    name = (user.first_name or "") + (" " + (user.last_name or "") if user.last_name else "")
    if not name.strip():
        name = user.username or None
    await platform_db.get_or_create_user(
        telegram_id=user.id,
        name=name.strip() or None,
    )

    # Отслеживание chat_id для групп
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        _known_chats.add(chat.id)


def is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь администратором."""
    admins = _load_admins()
    return user_id in admins


async def add_admin(user_id: int) -> None:
    """Добавить пользователя в список администраторов."""
    async with _admins_lock:
        admins = _load_admins()
        admins.add(user_id)
        _save_admins(admins)


def _get_admin_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура режима бога."""
    return ReplyKeyboardMarkup([
        [KeyboardButton("Пользователи"), KeyboardButton("Группы")],
        [KeyboardButton("События")],
        [KeyboardButton("Zenclass")],
    ], resize_keyboard=True, one_time_keyboard=False)


# Настройки по умолчанию
WEATHER_CITY_QUERY = "Moscow"
WEATHER_CITY_NAME = "Москва"

RATE_BASE_CURRENCIES = ["USD", "EUR", "CNY", "METALS"]  # USD, EUR, CNY, Золото/Серебро
RATE_QUOTE_CURRENCY = "RUB"

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

NOMINATIM_HEADERS = {"User-Agent": "TestELTelegramBot/1.0 (contact@example.com)"}
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


async def moon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /moon - информация о луне."""
    await ensure_user_saved(update)

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
    
    # Вычисляем дни до следующего полнолуния
    obs = ephem.Observer()
    obs.date = today
    next_full_date = ephem.Date(ephem.next_full_moon(obs.date)).datetime().date()
    days_to_full = (next_full_date - today).days
    
    # Если сегодня полнолуние (days_to_full == 0), берём следующее полнолуние после текущего цикла
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

    await update.message.reply_text("\n".join(parts), parse_mode='HTML')


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    await update.message.reply_text(
        "Привет! Я тестовый бот EL.\n\n"
        "Доступные команды:\n"
        "/weather - Погода по геолокации\n"
        "/rate - Курс валют\n"
        "/moon - Информация о луне\n\n"
        "Напиши команду или текст для меню.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /admin - режим бога."""
    await ensure_user_saved(update)
    user = update.effective_user
    if not user:
        return

    user_id = user.id

    # Проверка, является ли пользователь администратором
    if is_admin(user_id):
        # Показываем админ-панель с кнопками в нижней панели
        await update.message.reply_text(
            "Режим бога активирован.\nВыбери действие:",
            reply_markup=_get_admin_keyboard(),
        )
    else:
        # Запрашиваем код доступа и устанавливаем флаг ожидания
        context.user_data["waiting_admin_code"] = True
        await update.message.reply_text(
            "Для доступа к режиму бога введи код доступа.\n"
            "Отправь код в следующем сообщении:",
            reply_markup=ReplyKeyboardRemove(),
        )






def _format_time_iso(iso_str: str) -> str:
    """Из ISO времени оставить только HH:MM."""
    if not iso_str or "T" not in iso_str:
        return iso_str or "—"
    return iso_str.split("T")[1][:5]


async def _weather_at_coords(lat: float, lon: float, place_name: str, updated_at: Optional[str] = None, timezone_str: Optional[str] = None) -> Optional[str]:
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
    if updated_at:
        try:
            utc_time = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
            local_time_str = _format_local_time(utc_time, timezone_str)
            parts.append("")
            parts.append(f"Обновлено: {local_time_str}")
        except Exception:
            pass

    return "\n".join(parts)
async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    user = update.effective_user
    if not user:
        return
    
    uid = str(user.id)
    users = _load_users()
    user_data = users.get(uid, {})
    
    # Проверяем, есть ли сохраненная локация
    if "last_location" in user_data:
        loc = user_data["last_location"]
        lat = loc["lat"]
        lon = loc["lon"]
        updated_at = loc.get("updated_at")
        timezone_str = user_data.get("timezone")
        
        # Проверяем, прошло ли более 14 часов с последнего обновления локации
        location_expired = False
        if updated_at:
            try:
                last_update = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
                hours_passed = (datetime.utcnow() - last_update).total_seconds() / 3600
                if hours_passed > 14:  # Больше 14 часов
                    location_expired = True
            except Exception:
                location_expired = True
        
        # Если локация устарела, запрашиваем новую
        if location_expired:
            await weather_here(update, context)
            return
        
        # Получаем название места по координатам
        place_name = await _get_city_from_coords(lat, lon)
        if not place_name or place_name == "Локация":
            place_name = f"({lat:.4f}, {lon:.4f})"
        
        # Показываем погоду по последней локации
        text = await _weather_at_coords(lat, lon, place_name, updated_at, timezone_str)
        if not text:
            await update.message.reply_text("Не удалось получить погоду, попробуй позже.")
            return
        
        # Добавляем кнопку для обновления локации
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("Обновить локацию", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode='HTML')
    else:
        # Если локации нет, запрашиваем её
        await weather_here(update, context)


async def weather_here(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Отправить мою геопозицию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "Нажми кнопку, чтобы отправить свою геопозицию, и я покажу погоду для твоего места.",
        reply_markup=keyboard,
    )


async def weather_by_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.location:
        return
    
    # Сохраняем пользователя с локацией (ensure_user_saved сохранит локацию)
    await ensure_user_saved(update)
    
    user = update.effective_user
    if not user:
        return
    
    uid = str(user.id)
    users = _load_users()
    user_data = users.get(uid, {})
    
    remove_kb = ReplyKeyboardRemove()
    try:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        place_name = await _get_city_from_coords(lat, lon)
        if not place_name or place_name == "Локация":
            place_name = f"({lat:.4f}, {lon:.4f})"
        
        # Получаем время обновления и часовой пояс
        updated_at = None
        timezone_str = None
        if "last_location" in user_data:
            updated_at = user_data["last_location"].get("updated_at")
        if "timezone" in user_data:
            timezone_str = user_data["timezone"]
        
        text = await _weather_at_coords(lat, lon, place_name, updated_at, timezone_str)
        if not text:
            await update.message.reply_text(
                "Не удалось получить погоду по геолокации, попробуй позже.",
                reply_markup=remove_kb,
            )
            return
        await update.message.reply_text(text, reply_markup=remove_kb, parse_mode='HTML')
    except Exception as e:
        print(f"Ошибка погода по геолокации: {e!r}")
        await update.message.reply_text(
            "Не удалось получить погоду по геолокации, попробуй позже.",
            reply_markup=remove_kb,
        )


async def rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    buttons = []
    
    # Первая строка: USD, EUR, CNY
    buttons.append([
        InlineKeyboardButton(code, callback_data=f"rate:{code}")
        for code in RATE_BASE_CURRENCIES[:3]
    ])
    
    # Вторая строка: одна кнопка для золота и серебра
    buttons.append([
        InlineKeyboardButton("🥇🥈 Золото/Серебро", callback_data="rate:METALS")
    ])
    
    keyboard = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        f"Выбери валюту, для которой показать курс к {RATE_QUOTE_CURRENCY}:",
        reply_markup=keyboard,
    )


async def _fetch_rate(base_currency: str, quote_currency: str) -> Optional[float]:
    """Получить текущий курс валют через ExchangeRate-API или API ЦБ РФ для золота/серебра."""
    # Для золота и серебра используем API ЦБ РФ
    if base_currency in ("XAU", "XAG"):
        today = date.today()
        return await _fetch_historical_rate(base_currency, quote_currency, today)
    
    # Для остальных валют используем ExchangeRate-API
    url = f"https://open.er-api.com/v6/latest/{base_currency}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            if data.get("result") != "success":
                return None
            rates = data.get("rates") or {}
            if quote_currency not in rates:
                return None
            return float(rates[quote_currency])
    except Exception as e:
        print(f"Ошибка при запросе курса: {e!r}")
        return None


async def _fetch_historical_rate(base_currency: str, quote_currency: str, target_date: date) -> Optional[float]:
    """Получить исторический курс валют через API Центробанка России."""
    # Проверяем, что дата не в будущем
    today = date.today()
    if target_date > today:
        return None
    
    # Для золота и серебра используем отдельный endpoint драгоценных металлов
    if base_currency in ("XAU", "XAG"):
        date_str = target_date.strftime("%d/%m/%Y")
        url = f"https://www.cbr.ru/scripts/xml_metall.asp?date_req1={date_str}&date_req2={date_str}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                root = ET.fromstring(response.text)
                # Ищем нужный металл (золото или серебро)
                metal_code = "1" if base_currency == "XAU" else "2"  # 1 - золото, 2 - серебро
                for record in root.findall("Record"):
                    code = record.get("Code")
                    if code == metal_code:
                        buy_elem = record.find("Buy")
                        if buy_elem is not None:
                            # Цена за грамм в рублях
                            price_str = buy_elem.text.replace(",", ".")
                            return float(price_str)
                return None
        except Exception as e:
            print(f"Ошибка при запросе курса драгоценных металлов через ЦБ РФ: {e!r}")
            return None
    
    # Для остальных валют используем стандартный API ЦБ РФ
    date_str = target_date.strftime("%d/%m/%Y")
    url = f"https://www.cbr.ru/scripts/XML_daily.asp?date_req={date_str}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            # Парсим XML ответ
            root = ET.fromstring(response.text)
            # Ищем нужную валюту
            for valute in root.findall("Valute"):
                char_code = valute.find("CharCode")
                if char_code is not None and char_code.text == base_currency:
                    value_elem = valute.find("Value")
                    nominal_elem = valute.find("Nominal")
                    if value_elem is not None and nominal_elem is not None:
                        # Значение в формате "XX,XXXX" (запятая как разделитель)
                        value_str = value_elem.text.replace(",", ".")
                        nominal = int(nominal_elem.text)
                        # Курс за 1 единицу валюты к рублю
                        rate = float(value_str) / nominal
                        return rate
            return None
    except Exception as e:
        print(f"Ошибка при запросе исторического курса через ЦБ РФ: {e!r}")
        return None

def _calculate_percentage_change(current: float, previous: float) -> str:
    """Рассчитать процент изменения курса."""
    if previous == 0:
        return "—"
    change = ((current - previous) / previous) * 100
    if change > 0:
        return f"+{change:.2f}%"
    return f"{change:.2f}%"


async def rate_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатия на кнопку валюты."""
    query = update.callback_query
    if not query:
        return
    
    await query.answer()
    await ensure_user_saved(update)
    
    base_currency = query.data.split(":")[1]
    quote_currency = RATE_QUOTE_CURRENCY
    
    # Получаем смайлики для валют
    currency_emoji = {
        "USD": "💵",
        "EUR": "💶",
        "GBP": "💷",
        "JPY": "💴",
        "CNY": "💰",
        "XAU": "🥇",  # Золото
        "XAG": "🥈",  # Серебро
        "RUB": "💸"
    }
    quote_emoji = currency_emoji.get(quote_currency, "💱")
    
    # Специальная обработка для золота и серебра
    if base_currency == "METALS":
        # Получаем оба курса (золото и серебро)
        xau_rate = await _fetch_rate("XAU", quote_currency)
        xag_rate = await _fetch_rate("XAG", quote_currency)
        
        if xau_rate is None or xag_rate is None:
            await query.edit_message_text("Не удалось получить курс драгоценных металлов, попробуй позже.")
            return
        
        # Формируем сообщение с обоими курсами
        parts = [
            f"<b>{currency_emoji['XAU']} Курс XAU/{quote_currency}:</b>",
            f"  1 XAU = {xau_rate:.2f} {quote_currency}",
            "",
            f"<b>{currency_emoji['XAG']} Курс XAG/{quote_currency}:</b>",
            f"  1 XAG = {xag_rate:.2f} {quote_currency}",
        ]
        
        # Добавляем время обновления в самом низу
        user = update.effective_user
        if user:
            uid = str(user.id)
            users = _load_users()
            user_data = users.get(uid, {})
            timezone_str = user_data.get("timezone")
            now_utc = datetime.utcnow()
            local_time_str = _format_local_time(now_utc, timezone_str)
            parts.append("")
            parts.append(f"🕐 Обновлено: {local_time_str}")
        
        await query.edit_message_text("\n".join(parts), parse_mode='HTML')
        return
    
    # Для остальных валют
    # Получаем текущий курс
    current_rate = await _fetch_rate(base_currency, quote_currency)
    if current_rate is None:
        await query.edit_message_text("Не удалось получить курс валют, попробуй позже.")
        return
    
    # Получаем исторические курсы
    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    year_ago = today - timedelta(days=365)
    
    week_rate = await _fetch_historical_rate(base_currency, quote_currency, week_ago)
    month_rate = await _fetch_historical_rate(base_currency, quote_currency, month_ago)
    year_rate = await _fetch_historical_rate(base_currency, quote_currency, year_ago)
    
    base_emoji = currency_emoji.get(base_currency, "💱")
    
    # Формируем сообщение
    parts = [
        f"<b>{base_emoji} Курс {base_currency}/{quote_currency}:</b>",
        f"  1 {base_currency} = {current_rate:.2f} {quote_currency}",
        f"  100 {base_currency} = {current_rate * 100:.2f} {quote_currency}",
        "",
        "📊 История:",
    ]
    
    if week_rate:
        change = _calculate_percentage_change(current_rate, week_rate)
        diff_abs = current_rate - week_rate
        parts.append(f"  Неделя назад: {week_rate:.2f} {quote_currency} ({change}, {diff_abs:+.2f})")
    else:
        parts.append("  Неделя назад: данные недоступны")
    
    if month_rate:
        change = _calculate_percentage_change(current_rate, month_rate)
        diff_abs = current_rate - month_rate
        parts.append(f"  Месяц назад: {month_rate:.2f} {quote_currency} ({change}, {diff_abs:+.2f})")
    else:
        parts.append("  Месяц назад: данные недоступны")
    
    if year_rate:
        change = _calculate_percentage_change(current_rate, year_rate)
        diff_abs = current_rate - year_rate
        parts.append(f"  Год назад: {year_rate:.2f} {quote_currency} ({change}, {diff_abs:+.2f})")
    else:
        parts.append("  Год назад: данные недоступны")
    
    # Добавляем время обновления в самом низу
    user = update.effective_user
    if user:
        uid = str(user.id)
        users = _load_users()
        user_data = users.get(uid, {})
        timezone_str = user_data.get("timezone")
        now_utc = datetime.utcnow()
        local_time_str = _format_local_time(now_utc, timezone_str)
        parts.append("")
        parts.append(f"🕐 Обновлено: {local_time_str}")
    
    await query.edit_message_text("\n".join(parts), parse_mode='HTML')


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений."""
    await ensure_user_saved(update)
    
    user = update.effective_user
    if not user:
        return
    
    text = update.message.text.strip()
    user_id = user.id
    
    # Проверка кода администратора
    if context.user_data.get("waiting_admin_code"):
        if text == ADMIN_CODE:
            await add_admin(user_id)
            context.user_data["waiting_admin_code"] = False
            await update.message.reply_text(
                "Код верный! Режим бога активирован.\nВыбери действие:",
                reply_markup=_get_admin_keyboard(),
            )
        else:
            await update.message.reply_text(
                "Неверный код доступа. Попробуй еще раз или отправь /admin для повторной попытки.",
            )
        return

    # Ожидание email для создания студента Zenclass
    if context.user_data.get("awaiting_zenclass_email"):
        context.user_data["awaiting_zenclass_email"] = False
        if is_valid_email(text):
            ok = await zenclass_create_student_with_email(update, context, text)
            if ok:
                await update.message.reply_text(
                    "✅ Профиль студента создан в Zenclass и сохранён в базу platform_users."
                )
            else:
                await update.message.reply_text(
                    "❌ Не удалось создать студента. Проверьте email и токен API."
                )
        else:
            await update.message.reply_text(
                "❌ Неверный формат email. Отправьте email (например: user@example.com)"
            )
        return

    # Обработка команд администратора
    if is_admin(user_id):
        if text == "Пользователи":
            users = _load_users()
            count = len(users)
            await update.message.reply_text(f"Пользователей подписано: {count}")
        elif text == "Группы":
            groups_count = len(_known_chats)
            if groups_count == 0:
                await update.message.reply_text("Бот не состоит ни в одной группе.")
            else:
                groups_list = ", ".join(str(chat_id) for chat_id in _known_chats)
                await update.message.reply_text(f"Группы ({groups_count}): {groups_list}")
        elif text == "События":
            stats = events_storage.get_events_stats()
            type_labels = {"subscribe": "➕ Подписки", "unsubscribe": "👋 Отписки", "reaction": "❤️ Реакции"}
            parts = [
                "📊 <b>События (Telegram)</b>",
                "",
                f"Всего: {stats['total']}",
                f"  {type_labels['subscribe']}: {stats['subscribe']}",
                f"  {type_labels['unsubscribe']}: {stats['unsubscribe']}",
                f"  {type_labels['reaction']}: {stats['reaction']}",
            ]
            if stats["last_events"]:
                parts.append("")
                parts.append("Последние события:")
                for e in stats["last_events"][:5]:
                    ts = e.get("timestamp", "")[:10]
                    etype = e.get("type", "?")
                    chat_title = (e.get("chat") or {}).get("title", "—")
                    user = e.get("user") or {}
                    uname = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "—")
                    parts.append(f"  • {ts} | {etype} | {chat_title} | {uname}")
            await update.message.reply_text("\n".join(parts), parse_mode="HTML")
        elif text == "Zenclass":
            # Показываем меню Zenclass
            keyboard = get_zenclass_menu_keyboard()
            await update.message.reply_text(
                "🔧 Zenclass API\n\nВыбери действие:",
                reply_markup=keyboard,
            )
        elif text == "🔍 Тест API":
            await zenclass_test(update, context)
        elif text == "👥 Студенты":
            await zenclass_students(update, context)
        elif text == "📚 Курсы":
            await zenclass_courses(update, context)
        elif text == "➕ Создать студента":
            await zenclass_create_student_handler(update, context)
        elif text == "🔙 Назад":
            # Возвращаемся в главное меню админа
            await update.message.reply_text(
                "Режим бога.\nВыбери действие:",
                reply_markup=_get_admin_keyboard(),
            )
        else:
            # Если это не команда админа, проверяем другие команды
            if text == "Обновить локацию":
                await weather_here(update, context)
            else:
                await update.message.reply_text(
                    "Неизвестная команда. Используй /start для списка команд.",
                    reply_markup=ReplyKeyboardRemove(),
                )
    else:
        # Для обычных пользователей
        if text == "Обновить локацию":
            await weather_here(update, context)
        else:
            await update.message.reply_text(
                "Неизвестная команда. Используй /start для списка команд.",
                reply_markup=ReplyKeyboardRemove(),
            )


def main() -> None:
    """Запуск бота."""
    if not BOT_TOKEN:
        print("Ошибка: BOT_TOKEN не установлен в переменных окружения!")
        return

    events_storage.init_storage(BASE_DIR)

    application = Application.builder().token(BOT_TOKEN).build()

    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("weather", weather))
    application.add_handler(CommandHandler("rate", rate))
    application.add_handler(CommandHandler("moon", moon_cmd))
    application.add_handler(CommandHandler("admin", admin_cmd))

    # Обработчик геолокации
    application.add_handler(MessageHandler(filters.LOCATION, weather_by_location))

    # Обработчик callback для курсов валют
    application.add_handler(CallbackQueryHandler(rate_button, pattern=r"^rate:"))

    # Обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # События в группах/каналах: подписка, отписка, реакции
    subscribe_h = make_subscribe_handler(_load_admins, BASE_DIR)
    unsubscribe_h = make_unsubscribe_handler(_load_admins, BASE_DIR)
    reaction_h = make_reaction_handler(_load_admins, BASE_DIR)
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, subscribe_h))
    application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, unsubscribe_h))
    application.add_handler(MessageReactionHandler(reaction_h))

    # Запуск бота
    print("Бот запущен...")
    application.run_polling(allowed_updates=["message", "callback_query", "message_reaction"])


if __name__ == "__main__":
    main()

