import asyncio
import json
import os
from datetime import date, datetime
from typing import Any, Dict, Optional

import httpx
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
    filters,
)


load_dotenv()


BOT_TOKEN = os.getenv("BOT_TOKEN")

# Файл с данными пользователей (рядом с bot.py)
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")
_users_lock = asyncio.Lock()


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


async def ensure_user_saved(update: Update) -> None:
    """Обновить/добавить данные пользователя из update и сохранить в users.json."""
    user = update.effective_user
    if not user:
        return
    uid = str(user.id)
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name or "",
        "language_code": user.language_code or "",
        "is_premium": getattr(user, "is_premium", False),
        "last_seen": now,
    }
    async with _users_lock:
        users = _load_users()
        if uid not in users:
            record["first_seen"] = now
        else:
            record["first_seen"] = users[uid].get("first_seen", now)
        users[uid] = record
        _save_users(users)


# Настройки по умолчанию (можно поменять под себя)
WEATHER_CITY_QUERY = "Moscow"  # город по умолчанию (для /weather)
WEATHER_CITY_NAME = "Москва"

# Курс валют: какие показываем
RATE_BASE_CURRENCIES = ["USD", "EUR", "GBP", "CNY", "JPY"]
RATE_QUOTE_CURRENCY = "RUB"

# Краткие описания погоды по коду WMO (Open-Meteo)
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

# User-Agent для Nominatim (обязателен по правилам использования)
NOMINATIM_HEADERS = {"User-Agent": "MaraniusTelegramBot/1.0 (contact@example.com)"}

# Опорная дата новолуния для приблизительной фазы луны (упрощённый расчёт)
_NEW_MOON_REF = date(2000, 1, 6)
_LUNAR_DAYS = 29.53058867


def _moon_phase_name(d: date) -> str:
    """Приблизительная фаза луны по дате."""
    delta = (d - _NEW_MOON_REF).days
    phase = (delta % _LUNAR_DAYS) / _LUNAR_DAYS
    if phase < 0.03 or phase >= 0.97:
        return "новолуние"
    if 0.22 <= phase < 0.28:
        return "первая четверть"
    if 0.47 <= phase < 0.53:
        return "полнолуние"
    if 0.72 <= phase < 0.78:
        return "последняя четверть"
    if 0.03 <= phase < 0.47:
        return "растущая луна"
    return "убывающая луна"


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
        return (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
            or addr.get("county")
            or addr.get("state")
            or "Локация"
        )
    except Exception:
        return "Локация"


STORE_TEXT = (
    "Магическая лавка:\n\n"
    "Карты «Кристаллы Крайона» — 3 500 ₽\n"
    "Колода карт с практиками и посланиями.\n\n"
    "Чтобы оформить заказ:\n"
    "1) Нажми на кнопку/ссылку оплаты (которую мы позже сюда добавим),\n"
    "2) Или напиши мне в личные сообщения.\n"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    await update.message.reply_text(
        "Привет! Я бот Maranius.\n"
        "Моё меню:\n"
        "ip - Для владельцев колод (VIP)\n"
        "learning - Практики и курсы\n"
        "store - Магическая лавка\n"
        "services - Услуги Maranius\n"
        "info - Информация\n"
        "policy - Политика конфиденциальности\n"
        "weather - Погода (Москва)\n"
        "weather_here - Погода по геолокации\n"
        "rate - Курс валют\n\n"
        "Напиши одно из слов: ip, learning, store, services, info, policy, weather, weather_here или rate.\n"
        "Также доступны команды /weather, /weather_here, /rate и /me (что бот знает о тебе).",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip().lower()

    if text == "ip":
        await update.message.reply_text("Для владельцев колод (VIP)")
    elif text == "learning":
        await update.message.reply_text("Практики и курсы")
    elif text == "store":
        await update.message.reply_text(STORE_TEXT)
    elif text == "services":
        await update.message.reply_text("Услуги Maranius")
    elif text == "info":
        await update.message.reply_text("Информация")
    elif text == "policy":
        await update.message.reply_text("Политика конфиденциальности")
    elif text == "weather":
        await weather(update, context)
    elif text == "weather_here":
        await weather_here(update, context)
    elif text == "rate":
        await rate(update, context)
    else:
        await update.message.reply_text(
            "Я понимаю команды меню:\n"
            "ip, learning, store, services, info, policy.\n"
            "Пожалуйста, напиши одно из этих слов."
        )


async def vip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    await update.message.reply_text("Для владельцев колод (VIP)")


async def learning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    await update.message.reply_text("Практики и курсы")


async def store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    await update.message.reply_text(STORE_TEXT)


async def services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    await update.message.reply_text("Услуги Maranius")


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    await update.message.reply_text("Информация")


async def policy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    await update.message.reply_text("Политика конфиденциальности")


def _format_time_iso(iso_str: str) -> str:
    """Из ISO времени оставить только HH:MM."""
    if not iso_str or "T" not in iso_str:
        return iso_str or "—"
    return iso_str.split("T")[1][:5]


async def _weather_at_coords(lat: float, lon: float, place_name: str) -> Optional[str]:
    """Запрос погоды через Open-Meteo: сейчас + давление, влажность, восход/закат, луна, завтра."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&timezone=auto"
        "&current_weather=true"
        "&hourly=relative_humidity_2m,surface_pressure"
        "&daily=sunrise,sunset,temperature_2m_max,temperature_2m_min,weathercode"
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
    desc = WEATHER_CODES.get(int(code), "без осадков")

    # Текущий час для влажности/давления
    hourly = data.get("hourly") or {}
    h_times = hourly.get("time") or []
    humidity, pressure = None, None
    if h_times and cw.get("time"):
        try:
            cur = str(cw["time"])[:13]  # "2026-02-27T00"
            for i, t in enumerate(h_times):
                if str(t)[:13] == cur:
                    if "relative_humidity_2m" in hourly:
                        humidity = (hourly["relative_humidity_2m"] or [None])[i]
                    if "surface_pressure" in hourly:
                        pressure = (hourly["surface_pressure"] or [None])[i]
                    break
        except (IndexError, KeyError, TypeError):
            pass
    if humidity is None and (hourly.get("relative_humidity_2m")):
        humidity = (hourly["relative_humidity_2m"] or [None])[0]
    if pressure is None and (hourly.get("surface_pressure")):
        pressure = (hourly["surface_pressure"] or [None])[0]

    daily = data.get("daily") or {}
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []
    sunrise_str = _format_time_iso(sunrises[0]) if sunrises else "—"
    sunset_str = _format_time_iso(sunsets[0]) if sunsets else "—"

    today_date = date.today()
    moon_str = _moon_phase_name(today_date)

    parts = [
        f"Погода {place_name}:",
        f"  {desc.capitalize()}, {temp} °C",
    ]
    if wind is not None:
        parts.append(f"  Ветер: {wind} км/ч")
    if humidity is not None:
        parts.append(f"  Влажность: {humidity}%")
    if pressure is not None:
        parts.append(f"  Давление: {pressure} гПа")
    parts.append(f"  Восход: {sunrise_str}  ·  Закат: {sunset_str}")
    parts.append(f"  Луна: {moon_str}")

    # Прогноз на завтра
    d_times = daily.get("time") or []
    d_max = daily.get("temperature_2m_max") or []
    d_min = daily.get("temperature_2m_min") or []
    d_code = daily.get("weathercode") or []
    if len(d_times) >= 2 and len(d_max) >= 2 and len(d_min) >= 2:
        t_max = d_max[1]
        t_min = d_min[1]
        code_tom = d_code[1] if len(d_code) >= 2 else 0
        desc_tom = WEATHER_CODES.get(int(code_tom), "без осадков")
        parts.append("")
        parts.append("Завтра:")
        parts.append(f"  {desc_tom.capitalize()}, от {t_min} до {t_max} °C")

    return "\n".join(parts)


async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    # Сначала получаем координаты города через Open-Meteo Geocoding
    geo_url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={WEATHER_CITY_QUERY}&count=1"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(geo_url)
            response.raise_for_status()
            data = response.json()
        results = data.get("results") or []
        if not results:
            await update.message.reply_text("Город не найден.")
            return
        lat = results[0]["latitude"]
        lon = results[0]["longitude"]
    except Exception:
        await update.message.reply_text("Не удалось определить город, попробуй позже.")
        return

    text = await _weather_at_coords(lat, lon, WEATHER_CITY_NAME)
    if not text:
        await update.message.reply_text("Не удалось получить погоду, попробуй позже.")
        return
    await update.message.reply_text(text)


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
    await ensure_user_saved(update)
    if not update.message or not update.message.location:
        return

    remove_kb = ReplyKeyboardRemove()
    try:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        place_name = await _get_city_from_coords(lat, lon)
        text = await _weather_at_coords(lat, lon, place_name)
        if not text:
            await update.message.reply_text(
                "Не удалось получить погоду по геолокации, попробуй позже.",
                reply_markup=remove_kb,
            )
            return
        await update.message.reply_text(text, reply_markup=remove_kb)
    except Exception as e:
        print(f"Ошибка погода по геолокации: {e!r}")
        await update.message.reply_text(
            "Не удалось получить погоду по геолокации, попробуй позже.",
            reply_markup=remove_kb,
        )


async def rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    buttons = [
        [
            InlineKeyboardButton(code, callback_data=f"rate:{code}")
            for code in RATE_BASE_CURRENCIES[:3]
        ],
        [
            InlineKeyboardButton(code, callback_data=f"rate:{code}")
            for code in RATE_BASE_CURRENCIES[3:]
        ],
    ]
    keyboard = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        f"Выбери валюту, для которой показать курс к {RATE_QUOTE_CURRENCY}:",
        reply_markup=keyboard,
    )


async def _fetch_rate(base_currency: str, quote_currency: str) -> Optional[float]:
    # open.er-api.com (бесплатно, без ключа). Данные: ExchangeRate-API.
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


async def rate_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user_saved(update)
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 2:
        return

    base_currency = parts[1]
    quote_currency = RATE_QUOTE_CURRENCY

    value = await _fetch_rate(base_currency, quote_currency)
    if value is None:
        await query.message.reply_text(
            "Не удалось получить курс валют, попробуй позже."
        )
        return

    one_quote = value
    hundred_quote = value * 100
    msg = (
        f"Курс {base_currency} → {quote_currency}: {value:.2f}\n"
        f"  1 {base_currency} = {one_quote:.2f} {quote_currency}\n"
        f"  100 {base_currency} = {hundred_quote:.2f} {quote_currency}\n"
        "Источник: ExchangeRate-API (open.er-api.com)."
    )
    await query.message.reply_text(msg)


async def me_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать, какие данные о пользователе сохранены в боте."""
    await ensure_user_saved(update)
    user = update.effective_user
    if not user:
        return
    uid = str(user.id)
    users = _load_users()
    record = users.get(uid)
    if not record:
        await update.message.reply_text("Данные не найдены.")
        return
    name = (record.get("first_name") or "").strip()
    if record.get("last_name"):
        name = f"{name} {record['last_name']}".strip()
    username = record.get("username")
    lang = record.get("language_code") or "—"
    premium = "да" if record.get("is_premium") else "нет"
    first_seen = record.get("first_seen", "—")
    last_seen = record.get("last_seen", "—")
    uname = f"@{username}" if username else "—"
    msg = (
        "Данные, которые бот хранит о тебе:\n\n"
        f"ID: {record.get('id')}\n"
        f"Имя: {name or '—'}\n"
        f"Username: {uname}\n"
        f"Язык: {lang}\n"
        f"Premium: {premium}\n"
        f"Первый визит: {first_seen}\n"
        f"Последний визит: {last_seen}"
    )
    await update.message.reply_text(msg)


def main() -> None:
    token = BOT_TOKEN
    if not token:
        raise RuntimeError("Не найден токен бота. Укажи BOT_TOKEN в файле .env")

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("vip", vip))
    application.add_handler(CommandHandler("learning", learning))
    application.add_handler(CommandHandler("store", store))
    application.add_handler(CommandHandler("services", services))
    application.add_handler(CommandHandler("info", info))
    application.add_handler(CommandHandler("policy", policy))
    application.add_handler(CommandHandler("weather", weather))
    application.add_handler(CommandHandler("rate", rate))
    application.add_handler(CommandHandler("weather_here", weather_here))
    application.add_handler(CommandHandler("me", me_cmd))
    application.add_handler(MessageHandler(filters.LOCATION, weather_by_location))
    application.add_handler(CallbackQueryHandler(rate_button, pattern=r"^rate:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))

    print("Бот запущен. Нажми Ctrl+C, чтобы остановить.")
    application.run_polling()


if __name__ == "__main__":
    main()

