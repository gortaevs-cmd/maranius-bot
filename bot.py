import asyncio
import csv
import html
import json
import os
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

import httpx
import pytz
import ephem
from timezonefinder import TimezoneFinder
from dotenv import load_dotenv
from telegram import InputFile, MenuButtonCommands, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    MessageReactionHandler,
    filters,
)

# Явный путь к .env рядом с bot.py (не зависит от текущей папки в терминале и iCloud/BOM).
_BOT_DIR = Path(__file__).resolve().parent
load_dotenv(_BOT_DIR / ".env", override=True)

from integrations import (
    admin_audit,
    admin_alerts,
    analytics,
    consent_log,
    daily_practice,
    dice_content,
    ewasml_services,
    inbox as inbox_mod,
    platform_db,
    user_registry,
    vip_codes,
    vip_content,
)
from integrations.json_storage import JsonStorageError, load_json, pop_recovery_events, save_json
from events.handlers import (
    make_subscribe_handler,
    make_unsubscribe_handler,
    make_reaction_handler,
)
from events import storage as events_storage

import ui
from handlers import consent as consent_handlers
from handlers import profile as profile_handlers
from handlers import vip as vip_handlers

# Суперюзеры стенда — режим бога и VIP навсегда без кода в чат.
SEED_ADMIN_IDS: Set[int] = {186758977}


def _main_keyboard_for(user_id: int):
    """Нижнее меню с кнопкой рассылки, если пользователь не подписан."""
    return user_registry.main_reply_keyboard(
        _load_users(), user_id, seed_admin_ids=SEED_ADMIN_IDS
    )


def _env_strip(name: str) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip().strip("'\"")
    return v if v else None


def _token_numeric_id(token: Optional[str]) -> str:
    if not token or ":" not in token:
        return "?"
    return token.split(":", 1)[0].strip()


def resolve_bot_token() -> Tuple[Optional[str], str]:
    """
    Токен по BOT_PROFILE (test | prod).
    Для test только BOT_TOKEN_TEST — без fallback на BOT_TOKEN, иначе легко подхватить прод.
    Для prod: BOT_TOKEN_PROD или запасной BOT_TOKEN.
    """
    raw = (_env_strip("BOT_PROFILE") or "prod").lower()
    if raw in ("test", "dev", "local"):
        profile = "test"
    elif raw in ("prod", "production", "live"):
        profile = "prod"
    else:
        profile = "prod"
    if profile == "test":
        token = _env_strip("BOT_TOKEN_TEST")
    else:
        token = _env_strip("BOT_TOKEN_PROD") or _env_strip("BOT_TOKEN")
    return token, profile


BOT_TOKEN, BOT_PROFILE_ACTIVE = resolve_bot_token()
TELEGRAM_PROXY_URL = _env_strip("TELEGRAM_PROXY_URL") or ""
# Файлы с данными
# В production mutable JSON лежит на отдельном volume. Локальный запуск
# продолжает использовать корень проекта, поэтому старые стенды совместимы.
BASE_DIR = os.getenv("MARANIUS_RUNTIME_DIR") or str(_BOT_DIR)
USERS_FILE = os.path.join(BASE_DIR, "users.json")
ADMINS_FILE = os.path.join(BASE_DIR, "admins.json")
VIP_NOTIFY_FILE = os.path.join(BASE_DIR, "data", "vip", "admin_notify.json")
ADMIN_AUDIT_FILE = Path(BASE_DIR) / "admin_audit.json"
CONSENT_LOG_FILE = Path(BASE_DIR) / "consent_log.json"
# users_lock живёт в integrations/user_registry.py, чтобы быть рядом с данными.
_users_lock = user_registry.users_lock
_admins_lock = asyncio.Lock()
_vip_notify_lock = asyncio.Lock()

# Запрет пересылки VIP и ангельских ответов (ссылка @maraniuss кликабельна).
PROTECT_KWARGS = {"protect_content": True}

ADMIN_NOTIFY_COOLDOWN_SEC = 300

# Множество chat_id для отслеживания групп
_known_chats: Set[int] = set()


def _load_users() -> Dict[str, Any]:
    """Загрузить словарь user_id -> данные из users.json."""
    return user_registry.load_users()


def _save_users(users: Dict[str, Any]) -> None:
    """Сохранить словарь пользователей в users.json."""
    user_registry.save_users(users)


def _load_admins() -> Set[int]:
    """Загрузить множество admin user_id из admins.json."""
    data = load_json(Path(ADMINS_FILE), {"admins": []})
    if not isinstance(data, dict):
        raise ValueError(f"admins.json должен содержать JSON-объект: {ADMINS_FILE}")
    return set(data.get("admins", []))


def _save_admins(admins: Set[int]) -> None:
    """Сохранить множество администраторов в admins.json."""
    save_json(Path(ADMINS_FILE), {"admins": list(admins)}, trailing_newline=True)


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


async def ensure_user_saved(update: Update, *, bot=None, force: bool = False) -> bool:
    """
    Обновить/добавить данные пользователя в users.json.
    Returns True если пользователь новый (первый визит).
    Без force профиль не пишется до принятия актуальной политики (кроме seed-admin).
    """
    user = update.effective_user
    if not user:
        return False
    if not force and user.id not in SEED_ADMIN_IDS:
        if not user_registry.has_current_policy(_load_users(), user.id):
            return False
    uid = str(user.id)
    # После принятия политики уже может быть минимальная запись {id, policy_*}.
    # Новым считаем профиль, которому ещё не задавали first_seen.
    existing = _load_users().get(uid)
    is_new = not isinstance(existing, dict) or not existing.get("first_seen")

    async with _users_lock:
        users = _load_users()
        user_registry.merge_telegram_profile(
            users,
            user_id=user.id,
            username=user.username,
            first_name=user.first_name or "",
            last_name=user.last_name or "",
            language_code=user.language_code or "",
            is_premium=getattr(user, "is_premium", False),
            seed_admin_ids=SEED_ADMIN_IDS,
        )
        if user.id in SEED_ADMIN_IDS:
            users[uid]["vip"] = True
        _save_users(users)

    name = (user.first_name or "") + (" " + (user.last_name or "") if user.last_name else "")
    if not name.strip():
        name = user.username or None
    await platform_db.get_or_create_user(
        telegram_id=user.id,
        name=name.strip() or None,
    )

    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        _known_chats.add(chat.id)

    if is_new and bot:
        await admin_alerts.notify_new_subscriber(
            bot,
            SEED_ADMIN_IDS,
            user_id=user.id,
            username=user.username,
            first_name=user.first_name or "",
        )
    return is_new


def ensure_seed_admins() -> None:
    """Гарантировать seed-админов в admins.json (без ввода кода в чат)."""
    admins = _load_admins()
    if SEED_ADMIN_IDS.issubset(admins):
        return
    admins |= SEED_ADMIN_IDS
    _save_admins(admins)


def ensure_seed_vip() -> None:
    """Гарантировать seed-пользователям VIP в users.json (навсегда, как режим бога)."""
    users = _load_users()
    changed = False
    for user_id in SEED_ADMIN_IDS:
        if user_registry.grant_vip(users, user_id, source="seed_admin"):
            changed = True
        users.setdefault(str(user_id), {})["is_internal"] = True
        changed = True
    if changed:
        _save_users(users)


async def _save_user_location(uid: str, lat: float, lon: float) -> None:
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    timezone_str = _get_timezone_by_coords(lat, lon)
    async with _users_lock:
        users = _load_users()
        row = users.setdefault(uid, {})
        row["last_location"] = {"lat": lat, "lon": lon, "updated_at": now_str}
        if timezone_str:
            row["timezone"] = timezone_str
        _save_users(users)


async def _require_access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action_key: str,
) -> bool:
    return await consent_handlers.require_user_access(
        update,
        context,
        users_lock=_users_lock,
        load_users=_load_users,
        action_key=action_key,
        seed_admin_ids=SEED_ADMIN_IDS,
    )


def is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь администратором (файл + seed)."""
    if user_id in SEED_ADMIN_IDS:
        return True
    return user_id in _load_admins()


async def add_admin(user_id: int) -> None:
    """Добавить пользователя в список администраторов."""
    async with _admins_lock:
        admins = _load_admins()
        admins.add(user_id)
        _save_admins(admins)


# Настройки по умолчанию
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


async def moon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /moon или inline «Ещё → Луна»."""
    if not await _require_access(update, context, "moon"):
        return
    await ensure_user_saved(update, bot=context.bot)
    message = update.effective_message
    if not message:
        return
    text = _format_moon_text()
    if update.callback_query:
        await _edit_or_reply(message, text, ui.get_back_to_more_keyboard())
        return
    await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=_main_keyboard_for(update.effective_user.id)
        if update.effective_user
        else ui.get_main_keyboard(),
    )


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


async def sync_bot_commands(bot) -> int:
    """Синее меню «☰»: default + ru, кнопка меню = список команд."""
    commands = ui.get_bot_commands()
    for lang in (None, "ru", "en"):
        await bot.delete_my_commands(language_code=lang)
    await bot.set_my_commands(commands)
    await bot.set_my_commands(commands, language_code="ru")
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    return len(commands)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    # До согласия не сохраняем Telegram-профиль или параметр deep-link нового
    # пользователя. Параметр /start может быть данными для аналитики и пишется
    # только после отдельного активного согласия.
    # Seed-админ — внутренний служебный аккаунт.
    if user.id not in SEED_ADMIN_IDS:
        async with _users_lock:
            has_policy = user_registry.has_current_policy(_load_users(), user.id)
        if not has_policy:
            context.user_data["pending_action"] = "start"
            await consent_handlers.show_policy_gate(update, context)
            return

    await ensure_user_saved(update, bot=context.bot, force=user.id in SEED_ADMIN_IDS)
    if getattr(context, "args", None):
        payload = " ".join(context.args).strip()
        if payload:
            async with _users_lock:
                users = _load_users()
                user_registry.capture_start_param(users, user.id, payload)
                _save_users(users)
    async with _users_lock:
        users = _load_users()
        if users.get(str(user.id), {}).get("bot_status") == "blocked":
            user_registry.set_bot_status(users, user.id, "active")
            _save_users(users)
    if user.id in SEED_ADMIN_IDS:
        await add_admin(user.id)
    chat = update.effective_chat
    if chat:
        await context.bot.set_chat_menu_button(
            chat_id=chat.id,
            menu_button=MenuButtonCommands(),
        )
    await message.reply_text(
        ui.START_MESSAGE,
        reply_markup=_main_keyboard_for(user.id),
    )


async def _open_god_panel(update: Update) -> None:
    """Открыть корень режима бога (/god или текст god). Только seed-админы."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if user.id not in SEED_ADMIN_IDS:
        await message.reply_text(ui.ADMIN_DENIED)
        return
    await add_admin(user.id)
    await message.reply_text(
        ui.ADMIN_STUB,
        parse_mode="HTML",
        reply_markup=ui.get_admin_home_keyboard(),
    )


def _is_seed_admin(user_id: int) -> bool:
    """Проверка только seed-админов (для /god и управления пользователями)."""
    return user_id in SEED_ADMIN_IDS


async def god_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Скрытый /god: только для seed/admins, без запроса кода."""
    user = update.effective_user
    if not user or user.id not in SEED_ADMIN_IDS:
        await _open_god_panel(update)
        return
    await ensure_user_saved(update, force=True)
    await _open_god_panel(update)


async def _admin_edit_panel(message, text: str, keyboard) -> None:
    """Обновить сообщение панели или отправить новое, если edit недоступен."""
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _update_from_callback(query, update_id: int) -> Update:
    """Callback: effective_user = нажавший кнопку, не бот."""
    return Update(update_id, callback_query=query)


def _clear_vip_awaiting(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_vip_code", None)


def _clear_admin_input_mode(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("admin_mode", None)
    context.user_data.pop("admin_batch", None)
    context.user_data.pop("admin_user_action", None)
    context.user_data.pop("admin_confirm", None)


def _reset_admin_navigation(context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_admin_input_mode(context)
    context.user_data.pop("admin_selected_user", None)


WEATHER_LOCATION_TTL_HOURS = 4


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
    return hours is not None and hours <= WEATHER_LOCATION_TTL_HOURS


async def _save_weather_cache(uid: str, text: str) -> None:
    async with _users_lock:
        users = _load_users()
        if uid in users:
            users[uid]["last_weather_text"] = text
            _save_users(users)



async def _handle_card_pull(message, user_id: int) -> None:
    uid = str(user_id)
    async with _users_lock:
        state = daily_practice.practice_from_user(_load_users().get(uid))
    existing = state.get("card")
    if existing:
        await _edit_or_reply(
            message,
            ui.format_card_already(existing["title"], existing["url"]),
            ui.get_card_hub_back_keyboard(),
        )
        return

    await _edit_or_reply(message, ui.MSG_CARD_SHUFFLE, None)
    await asyncio.sleep(2.5)
    pull = daily_practice.pick_random_card()
    if not pull:
        await _edit_or_reply(
            message,
            ui.MSG_CARD_CATALOG_EMPTY,
            ui.get_card_hub_keyboard(),
        )
        return

    async with _users_lock:
        users = _load_users()
        state = daily_practice.practice_from_user(users.get(uid))
        concurrent_existing = state.get("card")
        if not concurrent_existing:
            users.setdefault(uid, {"id": user_id})
            users[uid]["daily_practice"] = daily_practice.apply_card_pull(state, pull)
            _save_users(users)
    if concurrent_existing:
        await _edit_or_reply(message, ui.format_card_already(concurrent_existing["title"], concurrent_existing["url"]), ui.get_card_hub_back_keyboard())
        return

    await _edit_or_reply(
        message,
        ui.format_card_success(pull["title"], pull["url"]),
        ui.get_card_hub_back_keyboard(),
    )


async def _handle_crystal_pull(message, user_id: int) -> None:
    uid = str(user_id)
    async with _users_lock:
        state = daily_practice.practice_from_user(_load_users().get(uid))
    existing = state.get("crystal")
    if existing:
        await _edit_or_reply(
            message,
            ui.format_crystal_already(existing["title"], existing["url"]),
            ui.get_card_hub_back_keyboard(),
        )
        return

    await _edit_or_reply(message, ui.MSG_CRYSTAL_SHUFFLE, None)
    await asyncio.sleep(2.5)
    pull = daily_practice.pick_random_crystal()
    if not pull:
        await _edit_or_reply(
            message,
            ui.MSG_CRYSTAL_CATALOG_EMPTY,
            ui.get_card_hub_keyboard(),
        )
        return

    async with _users_lock:
        users = _load_users()
        state = daily_practice.practice_from_user(users.get(uid))
        concurrent_existing = state.get("crystal")
        if not concurrent_existing:
            users.setdefault(uid, {"id": user_id})
            users[uid]["daily_practice"] = daily_practice.apply_crystal_pull(state, pull)
            _save_users(users)
    if concurrent_existing:
        await _edit_or_reply(message, ui.format_crystal_already(concurrent_existing["title"], concurrent_existing["url"]), ui.get_card_hub_back_keyboard())
        return

    await _edit_or_reply(
        message,
        ui.format_crystal_success(pull["title"], pull["url"]),
        ui.get_card_hub_back_keyboard(),
    )


async def _handle_dice_roll(message, user_id: int, bot) -> None:
    uid = str(user_id)
    async with _users_lock:
        state = daily_practice.practice_from_user(_load_users().get(uid))
    existing = state.get("dice")
    if existing:
        dice_text = dice_content.get_dice_message_html(existing["value"])
        await message.reply_text(
            ui.format_dice_already(dice_text),
            parse_mode="HTML",
        )
        return

    await message.reply_text(ui.MSG_DICE_INTRO, parse_mode="HTML")
    dice_msg = await bot.send_dice(chat_id=message.chat_id)
    await asyncio.sleep(3.5)
    value = dice_msg.dice.value if dice_msg.dice else 1
    value = value if 1 <= value <= 6 else 1

    async with _users_lock:
        users = _load_users()
        state = daily_practice.practice_from_user(users.get(uid))
        concurrent_existing = state.get("dice")
        if not concurrent_existing:
            users.setdefault(uid, {"id": user_id})
            users[uid]["daily_practice"] = daily_practice.apply_dice_roll(state, value)
            _save_users(users)

    if concurrent_existing:
        dice_text = dice_content.get_dice_message_html(concurrent_existing["value"])
        await dice_msg.reply_text(ui.format_dice_already(dice_text), parse_mode="HTML")
        return

    dice_text = dice_content.get_dice_message_html(value)
    await dice_msg.reply_text(dice_text, parse_mode="HTML")

async def _edit_or_reply(
    message,
    text: str,
    keyboard=None,
    *,
    parse_mode: Optional[str] = "HTML",
    **extra,
) -> None:
    kwargs = dict(extra)
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    try:
        await message.edit_text(text, reply_markup=keyboard, **kwargs)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        await message.reply_text(text, reply_markup=keyboard, **kwargs)
    except Exception:
        await message.reply_text(text, reply_markup=keyboard, **kwargs)


async def _reply_screen(
    update: Update,
    text: str,
    *,
    parse_mode: str = "HTML",
    inline_markup=None,
    protect: bool = False,
    disable_web_page_preview: bool = False,
) -> None:
    """Ответ экраном: inline отдельно; без inline — с нижним меню."""
    message = update.effective_message
    if not message:
        return
    extra = PROTECT_KWARGS if protect else {}
    if inline_markup:
        await message.reply_text(
            text,
            parse_mode=parse_mode,
            reply_markup=inline_markup,
            disable_web_page_preview=disable_web_page_preview,
            **extra,
        )
    else:
        uid = update.effective_user.id if update.effective_user else 0
        await message.reply_text(
            text,
            parse_mode=parse_mode,
            reply_markup=_main_keyboard_for(uid) if uid else ui.get_main_keyboard(),
            disable_web_page_preview=disable_web_page_preview,
            **extra,
        )


def _user_is_vip(user_id: int) -> bool:
    return user_registry.is_vip(_load_users(), user_id, seed_admin_ids=SEED_ADMIN_IDS)


async def _grant_vip_user(user_id: int, *, source: str = "code") -> None:
    async with _users_lock:
        users = _load_users()
        user_registry.grant_vip(users, user_id, source=source)
        _save_users(users)


async def show_vip_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """VIP: приветствие или запрос кода."""
    if not await _require_access(update, context, "vip"):
        return
    user = update.effective_user
    if not user:
        return
    if _user_is_vip(user.id):
        context.user_data.pop("awaiting_vip_code", None)
        await _reply_screen(
            update,
            vip_content.vip_home_html(),
            inline_markup=vip_content.deck_menu_keyboard(),
            protect=True,
        )
        return
    context.user_data["awaiting_vip_code"] = True
    text = f"<b>{ui.BTN_VIP}</b>\n\n{vip_content.no_access_html()}"
    await _reply_screen(update, text, protect=True)


async def show_vip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vip_home(update, context)


async def show_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context, "today"):
        return
    await ensure_user_saved(update, bot=context.bot)
    _clear_vip_awaiting(context)
    await _reply_screen(
        update,
        ui.MSG_TODAY,
        inline_markup=ui.get_today_inline_keyboard(),
    )


async def show_store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context, "store"):
        return
    await ensure_user_saved(update, bot=context.bot)
    _clear_vip_awaiting(context)
    await _reply_screen(
        update,
        ui.MSG_STORE_STUB,
        inline_markup=ui.get_store_inline_keyboard(),
        disable_web_page_preview=True,
    )


async def show_more(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context, "more"):
        return
    await ensure_user_saved(update, bot=context.bot)
    _clear_vip_awaiting(context)
    await _reply_screen(update, ui.MSG_MORE, inline_markup=ui.get_more_inline_keyboard())


async def show_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context, "contact"):
        return
    await ensure_user_saved(update, bot=context.bot)
    await _reply_screen(
        update,
        ui.MSG_CONTACT,
        inline_markup=ui.get_contact_inline_keyboard(),
        disable_web_page_preview=True,
    )


async def show_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context, "services"):
        return
    await ensure_user_saved(update, bot=context.bot)
    await _reply_screen(
        update,
        ui.MSG_SERVICES,
        inline_markup=ui.get_services_inline_keyboard(),
    )


async def show_learning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context, "learning"):
        return
    await ensure_user_saved(update, bot=context.bot)
    await _reply_screen(update, ui.MSG_LEARNING)


async def show_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context, "info"):
        return
    await ensure_user_saved(update, bot=context.bot)
    await _reply_screen(update, ui.MSG_INFO_FAQ)


async def show_policy(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    users = _load_users()
    user = update.effective_user
    with_marketing = bool(user and user_registry.has_current_policy(users, user.id))
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        ui.MSG_POLICY_FULL,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=ui.get_policy_keyboard(with_marketing=with_marketing),
    )


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_today(update, context)


async def vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vip(update, context)


async def store_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_store(update, context)


async def contact_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_contact(update, context)


async def learning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_learning(update, context)


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_info(update, context)


async def policy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_policy(update, context)






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


async def _weather_fetch_and_send(update: Update, user_data: Dict[str, Any]) -> None:
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
    main_kb = _main_keyboard_for(user.id)
    if not text:
        await message.reply_text(ui.MSG_WEATHER_FETCH_FAIL, reply_markup=main_kb)
        return
    await _save_weather_cache(uid, text)
    await message.reply_text(text, parse_mode="HTML", reply_markup=main_kb)


async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ещё → Погода: всегда новое сообщение, прогноз без inline-кнопок."""
    if not await _require_access(update, context, "weather"):
        return
    await ensure_user_saved(update, bot=context.bot)
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    uid = str(user.id)
    user_data = _load_users().get(uid, {})
    main_kb = _main_keyboard_for(user.id)

    if "last_location" not in user_data:
        await _weather_ask_location(message)
        return

    if not _location_is_fresh(user_data):
        await _weather_ask_location_expired(message)
        return

    cached = user_data.get("last_weather_text")
    if cached:
        hint = ui.MSG_WEATHER_CACHE_HINT.strip()
        display = cached if hint in cached else cached + ui.MSG_WEATHER_CACHE_HINT
        await message.reply_text(display, parse_mode="HTML", reply_markup=main_kb)
        return

    await _weather_fetch_and_send(update, user_data)


async def weather_ask_new_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Смена места до истечения 4 ч."""
    message = update.effective_message
    if not message:
        return
    await _weather_ask_location(message)


async def weather_by_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.location:
        return

    if not await _require_access(update, context, "weather"):
        return
    await ensure_user_saved(update, bot=context.bot)

    user = update.effective_user
    if not user:
        return

    uid = str(user.id)
    user_data = _load_users().get(uid, {})
    main_kb = _main_keyboard_for(user.id)

    try:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        await _save_user_location(uid, lat, lon)
        user_data = _load_users().get(uid, {})
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

        await _save_weather_cache(uid, text)
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=main_kb,
        )
    except Exception as e:
        print(f"Ошибка погода по геолокации: {e!r}")
        await update.message.reply_text(
            ui.MSG_WEATHER_GEO_FAIL,
            reply_markup=main_kb,
        )



async def reply_angelic_sign(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Ответ расшифровкой ангельского знака из локальных CSV."""
    if not update.message:
        return
    context.user_data["pending_angel_key"] = key
    if not await _require_access(update, context, "angel"):
        return
    context.user_data.pop("pending_angel_key", None)
    await ensure_user_saved(update, bot=context.bot)
    meaning, normalized = ewasml_services.lookup_angelic_sign(key)
    if meaning:
        display = normalized if normalized != key.strip() else key.strip()
        text = ui.format_angelic_sign(display, meaning)
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=_main_keyboard_for(update.effective_user.id)
            if update.effective_user
            else ui.get_main_keyboard(),
            **PROTECT_KWARGS,
        )
        return

    user = update.effective_user
    if user:
        entry = await ewasml_services.log_unknown_angelic(
            key,
            normalized,
            user_id=user.id,
            username=user.username,
        )
        if not entry.get("admin_notified_at"):
            sent = await admin_alerts.notify_inbox_entry(
                context.bot,
                SEED_ADMIN_IDS,
                user_id=user.id,
                username=user.username,
                entry_type="unknown_angel",
                text=key.strip(),
            )
            if sent:
                await inbox_mod.mark_notified(entry["id"])
    await update.message.reply_text(
        ui.MSG_ANGEL_NOT_FOUND.format(sign=key.strip()),
        parse_mode="HTML",
        reply_markup=_main_keyboard_for(user.id) if user else ui.get_main_keyboard(),
        **PROTECT_KWARGS,
    )



def _is_admin_unknown_angels_cmd(text: str) -> Optional[str]:
    """Legacy: перенаправление на inbox."""
    raw = (text or "").strip().casefold()
    if raw == ui.ADMIN_CMD_UNKNOWN_ANGELS.casefold():
        return "summary"
    if raw == ui.ADMIN_CMD_UNKNOWN_ANGELS_FILE.casefold():
        return "file"
    return None


def _user_stats() -> Dict[str, int]:
    return user_registry.stats_summary(_load_users())


async def _admin_guard(update: Update) -> bool:
    """Guard для inline-кнопок /god: только seed-админы."""
    user = update.effective_user
    if not user and update.callback_query:
        user = update.callback_query.from_user
    if user and user.id in SEED_ADMIN_IDS:
        return True
    message = update.effective_message
    if message:
        await message.reply_text(ui.ADMIN_DENIED)
    return False


async def admin_inbox(update: Update, mode: str) -> None:
    """Сводка или CSV inbox (только seed-admin)."""
    message = update.effective_message
    if not message or not await _admin_guard(update):
        return

    total, unnotified = await inbox_mod.stats()
    if total == 0:
        await message.reply_text(
            ui.ADMIN_INBOX_EMPTY,
            reply_markup=ui.get_admin_inbox_keyboard(),
        )
        return

    if mode == "summary":
        await message.reply_text(
            ui.ADMIN_INBOX_SUMMARY.format(total=total, unnotified=unnotified),
            parse_mode="HTML",
            reply_markup=ui.get_admin_inbox_keyboard(),
        )
        return

    csv_bytes, ts = await inbox_mod.export_csv_bytes()
    import io

    await message.reply_document(
        document=InputFile(io.BytesIO(csv_bytes), filename="inbox.csv"),
        caption=f"Inbox: {total} записей, скачано {ts}",
        reply_markup=ui.get_admin_inbox_keyboard(),
    )


async def admin_unknown_angels(update: Update, mode: str) -> None:
    """Legacy alias → inbox."""
    await admin_inbox(update, mode)


async def admin_export_list(update: Update, segment: Optional[str]) -> None:
    message = update.effective_message
    if not message or not await _admin_guard(update):
        return
    data = user_registry.export_users_csv(_load_users(), segment=segment)
    import io

    name = f"users_{segment or 'all'}.csv"
    await message.reply_document(
        document=InputFile(io.BytesIO(data), filename=name),
        caption=f"Сегмент: {segment or 'all'}",
        reply_markup=ui.get_admin_lists_keyboard(),
    )


_ADMIN_ACTIONS = {
    "vip": "VIP выдать",
    "grant_vip": "VIP выдать",
    "unvip": "VIP снять",
    "revoke_vip": "VIP снять",
    "block": "ограничить доступ",
    "unblock": "снять ограничение",
}


def _admin_reason(raw: str) -> Optional[str]:
    reason = " ".join((raw or "").split())
    if len(reason) < 3:
        return None
    return reason[:200]


async def _audit_admin_action(
    update: Update,
    *,
    action: str,
    target_ids=(),
    reason: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    actor = update.effective_user
    if not actor:
        return
    await admin_audit.append(
        ADMIN_AUDIT_FILE,
        actor_id=actor.id,
        action=action,
        target_ids=target_ids,
        reason=reason,
        meta=meta,
    )


async def _admin_exec_user_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cmd: str,
    uid: int,
    reason: str,
) -> None:
    """Выполнить изменение пользователя после подтверждения и записать аудит."""
    message = update.effective_message
    if uid in SEED_ADMIN_IDS:
        if message:
            await message.reply_text(ui.ADMIN_USER_PROTECTED)
        return

    changed = False
    action = ""
    audit_action = ""
    async with _users_lock:
        users = _load_users()
        if cmd in ("vip", "grant_vip"):
            changed = user_registry.grant_vip(users, uid, source="admin_grant")
            action = "VIP выдан" if changed else "VIP уже был выдан"
            audit_action = "vip_grant"
        elif cmd in ("unvip", "revoke_vip"):
            changed = user_registry.revoke_vip(users, uid)
            action = "VIP снят" if changed else "VIP уже был снят"
            audit_action = "vip_revoke"
        elif cmd == "block":
            changed = not user_registry.is_admin_blocked(users, uid)
            user_registry.set_admin_blocked(users, uid, True)
            action = "доступ ограничен" if changed else "доступ уже был ограничен"
            audit_action = "user_block"
        elif cmd == "unblock":
            changed = user_registry.is_admin_blocked(users, uid)
            user_registry.set_admin_blocked(users, uid, False)
            action = "ограничение снято" if changed else "ограничения уже не было"
            audit_action = "user_unblock"
        _save_users(users)

    await _audit_admin_action(
        update,
        action=audit_action,
        target_ids=[uid],
        reason=reason,
        meta={"changed": changed, "source": "god"},
    )

    if changed and cmd in ("vip", "grant_vip"):
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=ui.MSG_VIP_APPROVE_USER,
                parse_mode="HTML",
                reply_markup=_main_keyboard_for(uid),
            )
        except Exception as exc:
            print(f"VIP notify user {uid}: {exc!r}")

    if message:
        await message.reply_text(
            ui.ADMIN_USER_CMD_OK.format(action=action, target=uid) + " Журнал обновлён.",
            parse_mode="HTML",
            reply_markup=ui.get_admin_users_manage_keyboard(),
        )


async def _admin_queue_user_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cmd: str,
    uid: int,
    reason: str,
) -> None:
    context.user_data["admin_confirm"] = {"cmd": cmd, "target": uid, "reason": reason}
    await update.effective_message.reply_text(
        ui.ADMIN_CONFIRM_PROMPT.format(
            action=_ADMIN_ACTIONS[cmd], target=uid, reason=html.escape(reason)
        ),
        parse_mode="HTML",
        reply_markup=ui.get_admin_confirm_keyboard(),
    )


async def _admin_apply_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Текстовые команды /god с обязательным основанием."""
    parts = text.split()
    if not parts or parts[0].casefold() not in _ADMIN_ACTIONS:
        return False
    cmd = parts[0].casefold()
    if len(parts) < 2:
        await update.message.reply_text(ui.ADMIN_USER_CMD_REASON_REQUIRED, parse_mode="HTML")
        return True
    target_raw = parts[1]
    uid = user_registry.parse_user_ref(target_raw)
    if uid is None:
        uid = user_registry.find_user_id_by_username(_load_users(), target_raw)
    if uid is None:
        await update.message.reply_text(
            ui.ADMIN_USER_CMD_FAIL.format(target=html.escape(target_raw)), parse_mode="HTML"
        )
        return True
    reason = _admin_reason(" ".join(parts[2:]))
    if not reason:
        await update.message.reply_text(ui.ADMIN_USER_CMD_REASON_REQUIRED, parse_mode="HTML")
        return True
    await _admin_queue_user_action(update, context, cmd=cmd, uid=uid, reason=reason)
    return True


def _admin_user_card_text(uid: int, row: Dict[str, Any]) -> str:
    username = (row.get("username") or "").strip()
    username_text = f"@{html.escape(username)}" if username else "—"
    vip_source = html.escape(user_registry.vip_source_label(row.get("vip_source")))
    return ui.ADMIN_USER_CARD.format(
        user_id=uid,
        username=username_text,
        vip="да" if row.get("vip") else "нет",
        vip_source=vip_source,
        bot_status="заблокировал" if row.get("bot_status") == "blocked" else "доступен",
        admin_blocked="да" if row.get("admin_blocked") else "нет",
        marketing="да" if user_registry.has_current_marketing_consent(_load_users(), uid) else "нет",
        policy="да" if user_registry.has_current_policy(_load_users(), uid) else "нет",
        last_seen=html.escape(str(row.get("last_seen") or "—")),
    )


async def _admin_find_user(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str) -> None:
    target = (raw or "").strip()
    uid = user_registry.parse_user_ref(target)
    users = _load_users()
    if uid is None:
        uid = user_registry.find_user_id_by_username(users, target)
    row = users.get(str(uid)) if uid is not None else None
    if uid is None or not isinstance(row, dict):
        await update.effective_message.reply_text(
            ui.ADMIN_USER_CMD_FAIL.format(target=html.escape(target)), parse_mode="HTML",
            reply_markup=ui.get_admin_users_manage_keyboard(),
        )
        return
    context.user_data["admin_selected_user"] = uid
    await update.effective_message.reply_text(
        _admin_user_card_text(uid, row),
        parse_mode="HTML",
        reply_markup=ui.get_admin_user_card_keyboard(),
    )


async def _admin_prepare_selected_user_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cmd: str
) -> None:
    uid = context.user_data.get("admin_selected_user")
    if not isinstance(uid, int) or cmd not in _ADMIN_ACTIONS:
        await update.effective_message.reply_text(
            ui.ADMIN_MENU_USERS,
            parse_mode="HTML",
            reply_markup=ui.get_admin_users_manage_keyboard(),
        )
        return
    context.user_data["admin_user_action"] = {"cmd": cmd, "target": uid}
    context.user_data["admin_mode"] = "user_action_reason"
    await update.effective_message.reply_text(
        f"{_ADMIN_ACTIONS[cmd]} для <code>{uid}</code>. {ui.ADMIN_BATCH_REASON_PROMPT}",
        parse_mode="HTML",
        reply_markup=ui.get_admin_users_manage_keyboard(),
    )


async def _admin_receive_selected_user_reason(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    pending = context.user_data.pop("admin_user_action", None)
    reason = _admin_reason(text)
    context.user_data.pop("admin_mode", None)
    if not isinstance(pending, dict) or not reason:
        await update.effective_message.reply_text(ui.ADMIN_USER_CMD_REASON_REQUIRED, parse_mode="HTML")
        return
    await _admin_queue_user_action(
        update,
        context,
        cmd=pending["cmd"],
        uid=pending["target"],
        reason=reason,
    )


async def _admin_prepare_vip_codes(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    added, duplicates, skipped = await vip_codes.preview_codes_bulk(text)
    if added == 0:
        await update.effective_message.reply_text(
            "Нет новых кодов для добавления. Проверь список и попробуй ещё раз.",
            reply_markup=ui.get_admin_vip_prompt_keyboard(),
        )
        return
    context.user_data["admin_batch"] = {
        "kind": "vip_codes",
        "raw": text,
        "preview": {"added": added, "duplicates": duplicates, "skipped": skipped},
    }
    context.user_data["admin_mode"] = "vip_codes_reason"
    await update.effective_message.reply_text(ui.ADMIN_BATCH_REASON_PROMPT, parse_mode="HTML")


async def _admin_prepare_vip_import(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    ids, invalid = vip_codes.parse_vip_user_ids(text)
    unique_ids = list(dict.fromkeys(ids))
    duplicates = len(ids) - len(unique_ids)
    if not unique_ids:
        await update.effective_message.reply_text(
            "Не нашёл корректных Telegram ID. Пришли список ещё раз.",
            reply_markup=ui.get_admin_vip_prompt_keyboard(),
        )
        return
    async with _users_lock:
        users = _load_users()
        granted = sum(not bool(users.get(str(uid), {}).get("vip")) for uid in unique_ids)
    context.user_data["admin_batch"] = {
        "kind": "vip_import",
        "ids": unique_ids,
        "invalid": invalid,
        "duplicates": duplicates,
        "preview": {"granted": granted, "skipped": len(unique_ids) - granted},
    }
    context.user_data["admin_mode"] = "vip_import_reason"
    await update.effective_message.reply_text(ui.ADMIN_BATCH_REASON_PROMPT, parse_mode="HTML")


async def _admin_receive_batch_reason(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    batch = context.user_data.get("admin_batch")
    reason = _admin_reason(text)
    context.user_data.pop("admin_mode", None)
    if not isinstance(batch, dict) or not reason:
        context.user_data.pop("admin_batch", None)
        await update.effective_message.reply_text(ui.ADMIN_USER_CMD_REASON_REQUIRED, parse_mode="HTML")
        return
    batch["reason"] = reason
    context.user_data["admin_batch"] = batch
    if batch["kind"] == "vip_codes":
        text_out = ui.ADMIN_BATCH_CODE_PREVIEW.format(
            added=batch["preview"]["added"],
            dup=batch["preview"]["duplicates"],
            skipped=batch["preview"]["skipped"],
            reason=html.escape(reason),
        )
    else:
        text_out = ui.ADMIN_BATCH_IMPORT_PREVIEW.format(
            granted=batch["preview"]["granted"],
            skipped=batch["preview"]["skipped"],
            duplicates=batch["duplicates"],
            invalid=batch["invalid"],
            reason=html.escape(reason),
        )
    await update.effective_message.reply_text(
        text_out,
        parse_mode="HTML",
        reply_markup=ui.get_admin_batch_confirm_keyboard(),
    )


async def _admin_confirm_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    batch = context.user_data.pop("admin_batch", None)
    message = update.effective_message
    if not isinstance(batch, dict) or not message:
        return
    if batch.get("kind") == "vip_codes":
        added, duplicates, skipped = await vip_codes.add_codes_bulk(batch["raw"])
        await _audit_admin_action(
            update,
            action="vip_codes_added",
            reason=batch["reason"],
            meta={"added": added, "duplicates": duplicates, "skipped": skipped},
        )
        await message.reply_text(
            ui.ADMIN_VIP_ADD_RESULT.format(added=added, dup=duplicates, skipped=skipped),
            parse_mode="HTML",
            reply_markup=ui.get_admin_vip_keyboard(),
        )
        return
    if batch.get("kind") == "vip_import":
        granted = skipped = 0
        async with _users_lock:
            users = _load_users()
            for uid in batch["ids"]:
                if user_registry.grant_vip(users, uid, source="import"):
                    granted += 1
                else:
                    skipped += 1
            _save_users(users)
        await _audit_admin_action(
            update,
            action="vip_import",
            target_ids=batch["ids"],
            reason=batch["reason"],
            meta={
                "granted": granted,
                "already_vip": skipped,
                "duplicates": batch["duplicates"],
                "invalid": batch["invalid"],
            },
        )
        await message.reply_text(
            ui.ADMIN_VIP_IMPORT_RESULT.format(
                granted=granted,
                skipped=skipped,
                duplicates=batch["duplicates"],
                invalid=batch["invalid"],
            ),
            parse_mode="HTML",
            reply_markup=ui.get_admin_vip_keyboard(),
        )


def _admin_audit_row(entry: Dict[str, Any]) -> str:
    actions = {
        "vip_grant": "выдача VIP",
        "vip_grant_from_alert": "выдача VIP по алерту",
        "vip_reject_from_alert": "отклонение VIP по алерту",
        "vip_revoke": "снятие VIP",
        "user_block": "ограничение доступа",
        "user_unblock": "снятие ограничения",
        "vip_import": "импорт VIP",
        "vip_codes_added": "добавление кодов",
    }
    targets = [str(value) for value in entry.get("target_ids", [])]
    target_text = ", ".join(targets[:3]) or "—"
    if len(targets) > 3:
        target_text += f" +{len(targets) - 3}"
    return (
        f"• <code>{html.escape(str(entry.get('created_at') or ''))}</code> — "
        f"{html.escape(actions.get(str(entry.get('action')), str(entry.get('action') or 'действие')))}; "
        f"цели: <code>{html.escape(target_text)}</code>; "
        f"{html.escape(str(entry.get('reason') or '—'))}"
    )


async def admin_audit_summary(update: Update) -> None:
    message = update.effective_message
    if not message or not await _admin_guard(update):
        return
    entries = await admin_audit.recent(ADMIN_AUDIT_FILE)
    if not entries:
        await message.reply_text(ui.ADMIN_AUDIT_EMPTY, reply_markup=ui.get_admin_audit_keyboard())
        return
    await message.reply_text(
        ui.ADMIN_AUDIT_SUMMARY.format(rows="\n".join(_admin_audit_row(entry) for entry in entries)),
        parse_mode="HTML",
        reply_markup=ui.get_admin_audit_keyboard(),
    )


async def admin_audit_export(update: Update) -> None:
    message = update.effective_message
    if not message or not await _admin_guard(update):
        return
    data = await admin_audit.export_csv_bytes(ADMIN_AUDIT_FILE)
    import io

    await message.reply_document(
        document=InputFile(io.BytesIO(data), filename="admin_audit.csv"),
        caption="Журнал действий /god",
        reply_markup=ui.get_admin_audit_keyboard(),
    )


def _admin_consent_row(entry: Dict[str, Any]) -> str:
    events = {
        "policy_accepted": "ПДн принято",
        "marketing_opt_in": "рассылка: да",
        "marketing_opt_out": "рассылка: нет",
    }
    event = events.get(str(entry.get("event")), str(entry.get("event") or "согласие"))
    source = str(entry.get("source") or "—")
    version = str(entry.get("policy_version") or "—")
    document = str(entry.get("document") or "—")
    action = str(entry.get("action") or "—")
    return (
        f"• <code>{html.escape(str(entry.get('created_at') or ''))}</code> — "
        f"user <code>{html.escape(str(entry.get('user_id') or ''))}</code>: "
        f"{html.escape(event)}; источник: {html.escape(source)}; "
        f"документ: {html.escape(document)}; версия: {html.escape(version)}; "
        f"действие: {html.escape(action)}"
    )


async def admin_consent_summary(update: Update) -> None:
    message = update.effective_message
    if not message or not await _admin_guard(update):
        return
    entries = await consent_log.recent(CONSENT_LOG_FILE, limit=10)
    if not entries:
        await message.reply_text(ui.ADMIN_CONSENT_EMPTY, reply_markup=ui.get_admin_audit_keyboard())
        return
    await message.reply_text(
        ui.ADMIN_CONSENT_SUMMARY.format(rows="\n".join(_admin_consent_row(entry) for entry in entries)),
        parse_mode="HTML",
        reply_markup=ui.get_admin_audit_keyboard(),
    )


async def admin_consent_export(update: Update) -> None:
    message = update.effective_message
    if not message or not await _admin_guard(update):
        return
    data = await consent_log.export_csv_bytes(CONSENT_LOG_FILE)
    import io

    await message.reply_document(
        document=InputFile(io.BytesIO(data), filename="consent_log.csv"),
        caption="Журнал согласий",
        reply_markup=ui.get_admin_audit_keyboard(),
    )


async def admin_users_summary(update: Update) -> None:
    message = update.effective_message
    if not message or not await _admin_guard(update):
        return
    st = _user_stats()
    await message.reply_text(
        ui.ADMIN_USERS_SUMMARY.format(
            total=st["total"],
            real=st["real"],
            week=st["new_7d"],
            vip=st["vip"],
            no_policy=st["no_policy"],
            marketing=st["marketing"],
            sleeping=st["sleeping"],
        ),
        parse_mode="HTML",
        reply_markup=ui.get_admin_bot_keyboard(),
    )


async def admin_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not await _admin_guard(update):
        return
    me = await context.bot.get_me()
    username = me.username or "?"
    cmd_count = len(ui.get_bot_commands())
    await message.reply_text(
        ui.ADMIN_STATUS.format(
            profile=BOT_PROFILE_ACTIVE,
            username=username,
            cmd_count=cmd_count,
        ),
        parse_mode="HTML",
        reply_markup=ui.get_admin_bot_keyboard(),
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-кнопки режима бога."""
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    if not await _admin_guard(update):
        return
    data = query.data or ""

    if data == ui.CB_ADMIN_HOME:
        _reset_admin_navigation(context)
        await _admin_edit_panel(query.message, ui.ADMIN_STUB, ui.get_admin_home_keyboard())
        return
    if data == ui.CB_ADMIN_MENU_INBOX or data == ui.CB_ADMIN_MENU_ANGELS:
        _reset_admin_navigation(context)
        await _admin_edit_panel(
            query.message, ui.ADMIN_MENU_INBOX, ui.get_admin_inbox_keyboard()
        )
        return
    if data == ui.CB_ADMIN_MENU_LISTS:
        _reset_admin_navigation(context)
        await _admin_edit_panel(
            query.message, ui.ADMIN_LISTS_HINT, ui.get_admin_lists_keyboard()
        )
        return
    if data == ui.CB_ADMIN_MENU_USERS:
        _reset_admin_navigation(context)
        await _admin_edit_panel(
            query.message, ui.ADMIN_MENU_USERS, ui.get_admin_users_manage_keyboard()
        )
        return
    if data == ui.CB_ADMIN_MENU_BOT:
        _reset_admin_navigation(context)
        await _admin_edit_panel(query.message, ui.ADMIN_MENU_BOT, ui.get_admin_bot_keyboard())
        return
    if data == ui.CB_ADMIN_MENU_VIP:
        _reset_admin_navigation(context)
        await _admin_edit_panel(query.message, ui.ADMIN_MENU_VIP, ui.get_admin_vip_keyboard())
        return
    if data == ui.CB_ADMIN_MENU_AUDIT:
        _reset_admin_navigation(context)
        await _admin_edit_panel(query.message, ui.ADMIN_MENU_AUDIT, ui.get_admin_audit_keyboard())
        return

    if data in (ui.CB_ADMIN_UNKNOWN, ui.CB_ADMIN_INBOX):
        await admin_inbox(update, "summary")
        return
    if data in (ui.CB_ADMIN_UNKNOWN_CSV, ui.CB_ADMIN_INBOX_CSV):
        await admin_inbox(update, "file")
        return
    if data == ui.CB_ADMIN_LIST_ALL:
        await admin_export_list(update, None)
        return
    if data.startswith(ui.CB_ADMIN_LIST_SEGMENT_PREFIX) and data != ui.CB_ADMIN_LIST_ALL:
        seg = data[len(ui.CB_ADMIN_LIST_SEGMENT_PREFIX) :]
        await admin_export_list(update, seg)
        return
    if data == ui.CB_ADMIN_USERS:
        await admin_users_summary(update)
        return
    if data == ui.CB_ADMIN_STATUS:
        await admin_bot_status(update, context)
        return
    if data == ui.CB_ADMIN_AUDIT:
        await admin_audit_summary(update)
        return
    if data == ui.CB_ADMIN_AUDIT_CSV:
        await admin_audit_export(update)
        return
    if data == ui.CB_ADMIN_CONSENT:
        await admin_consent_summary(update)
        return
    if data == ui.CB_ADMIN_CONSENT_CSV:
        await admin_consent_export(update)
        return
    if data == ui.CB_ADMIN_USER_FIND:
        _clear_admin_input_mode(context)
        context.user_data["admin_mode"] = "user_lookup"
        await query.message.reply_text(
            ui.ADMIN_USER_FIND_PROMPT,
            reply_markup=ui.get_admin_users_manage_keyboard(),
        )
        return
    if data == ui.CB_ADMIN_USER_BACK:
        _reset_admin_navigation(context)
        await _admin_edit_panel(
            query.message, ui.ADMIN_MENU_USERS, ui.get_admin_users_manage_keyboard()
        )
        return
    user_actions = {
        ui.CB_ADMIN_USER_VIP_ON: "vip",
        ui.CB_ADMIN_USER_VIP_OFF: "unvip",
        ui.CB_ADMIN_USER_BLOCK: "block",
        ui.CB_ADMIN_USER_UNBLOCK: "unblock",
    }
    if data in user_actions:
        await _admin_prepare_selected_user_action(update, context, user_actions[data])
        return
    if data == ui.CB_ADMIN_VIP:
        await vip_handlers.admin_vip_summary(update, _admin_guard)
        return
    if data == ui.CB_ADMIN_VIP_EXPORT:
        await vip_handlers.admin_vip_export(update, _admin_guard)
        return
    if data == ui.CB_ADMIN_VIP_ADD:
        await vip_handlers.admin_vip_add_prompt(update, context, _admin_guard)
        return
    if data == ui.CB_ADMIN_VIP_IMPORT:
        await vip_handlers.admin_vip_import_prompt(update, context, _admin_guard)
        return
    if data == ui.CB_ADMIN_VIP_CANCEL:
        _clear_admin_input_mode(context)
        await _admin_edit_panel(
            query.message,
            ui.ADMIN_MENU_VIP,
            ui.get_admin_vip_keyboard(),
        )
        return
    if data == ui.CB_ADMIN_BATCH_CONFIRM:
        await _admin_confirm_batch(update, context)
        return
    if data == ui.CB_ADMIN_BATCH_CANCEL:
        _clear_admin_input_mode(context)
        await _admin_edit_panel(
            query.message,
            ui.ADMIN_MENU_VIP,
            ui.get_admin_vip_keyboard(),
        )
        return
    if data == ui.CB_ADMIN_CONFIRM:
        confirm = context.user_data.pop("admin_confirm", None)
        if confirm:
            await _admin_exec_user_cmd(
                update,
                context,
                confirm["cmd"],
                confirm["target"],
                confirm["reason"],
            )
        else:
            await _admin_edit_panel(
                query.message, ui.ADMIN_MENU_USERS, ui.get_admin_users_manage_keyboard()
            )
        return
    if data == ui.CB_ADMIN_CANCEL:
        context.user_data.pop("admin_confirm", None)
        await _admin_edit_panel(
            query.message, ui.ADMIN_MENU_USERS, ui.get_admin_users_manage_keyboard()
        )
        return


async def vip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await vip_handlers.vip_menu_callback(
        update,
        context,
        is_vip=_user_is_vip,
        protect_kwargs=PROTECT_KWARGS,
    )


async def vip_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def _grant(uid: int) -> None:
        await _grant_vip_user(uid, source="admin_grant")

    async def _audit(action: str, uid: int) -> None:
        await _audit_admin_action(
            update,
            action=action,
            target_ids=[uid],
            reason="Решение по алерту о неверном VIP-коде",
            meta={"source": "vip_alert"},
        )

    await vip_handlers.vip_approve_callback(
        update,
        context,
        admin_guard=_admin_guard,
        grant_vip=_grant,
        main_keyboard_for=_main_keyboard_for,
        audit_action=_audit,
    )


async def today_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-кнопки экрана «Сегодня»."""
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    if not await _require_access(update, context, "today"):
        return
    _clear_vip_awaiting(context)
    data = query.data or ""
    msg = query.message

    if data == ui.CB_TODAY_HOME:
        await _edit_or_reply(msg, ui.MSG_TODAY, ui.get_today_inline_keyboard())
        return

    if data == ui.CB_TODAY_ANGEL:
        await _edit_or_reply(msg, ui.MSG_ANGEL_INSTRUCTION, ui.get_today_back_keyboard())
        return

    if data == ui.CB_TODAY_CARD or data == ui.CB_CARD_BACK:
        await _edit_or_reply(
            msg,
            ui.MSG_CARD_HUB,
            ui.get_card_hub_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data == ui.CB_TODAY_DICE:
        user = query.from_user
        if user:
            await _handle_dice_roll(msg, user.id, context.bot)
        return

    if data == ui.CB_CARD_PULL:
        user = query.from_user
        if user:
            await _handle_card_pull(msg, user.id)
        return

    if data == ui.CB_CRYSTAL_PULL:
        user = query.from_user
        if user:
            await _handle_crystal_pull(msg, user.id)
        return



async def more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-кнопки экрана «Ещё»."""
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    if not await _require_access(update, context, "more"):
        return
    _clear_vip_awaiting(context)
    data = query.data or ""
    msg = query.message
    cb_update = _update_from_callback(query, update.update_id)

    if data == ui.CB_MORE_HOME:
        await _edit_or_reply(msg, ui.MSG_MORE, ui.get_more_inline_keyboard())
        return

    if data == ui.CB_WEATHER:
        await weather(cb_update, context)
        return
    if data == ui.CB_MOON:
        await moon_cmd(cb_update, context)
        return
    if data == ui.CB_SERVICES:
        await _edit_or_reply(
            msg,
            ui.MSG_SERVICES,
            ui.get_services_inline_keyboard(with_back=True),
        )
        return
    if data == ui.CB_LEARNING:
        await _edit_or_reply(
            msg,
            ui.MSG_LEARNING,
            ui.get_back_to_more_keyboard(),
        )
        return
    if data == ui.CB_INFO:
        await _edit_or_reply(
            msg,
            ui.MSG_INFO_FAQ,
            ui.get_back_to_more_keyboard(),
            disable_web_page_preview=True,
        )
        return
    if data == ui.CB_MORE_PROFILE:
        await _edit_or_reply(msg, ui.MSG_PROFILE_MENU, ui.get_profile_menu_keyboard())
        return
    if data == ui.CB_PROFILE_STATUS:
        user = query.from_user
        if not user:
            return
        row = user_registry.get_user(_load_users(), user.id)
        courses = await platform_db.get_user_courses(str(user.id))
        text = profile_handlers.format_status_html(
            row,
            is_vip=_user_is_vip(user.id),
            courses=courses,
        )
        await _edit_or_reply(msg, text, ui.get_profile_status_back_keyboard())
        return
    if data == ui.CB_PROFILE_SUBS:
        user = query.from_user
        if not user:
            return
        users = _load_users()
        opt_in = user_registry.has_current_marketing_consent(users, user.id)
        await _edit_or_reply(
            msg,
            profile_handlers.subscriptions_message(marketing_opt_in=opt_in),
            ui.get_profile_subs_keyboard(marketing_opt_in=opt_in),
        )
        return
    if data == ui.CB_PROFILE_SUB_ON:
        context.user_data["pending_action"] = "marketing_subscribe"
        await consent_handlers.show_marketing_offer(update)
        return
    if data == ui.CB_PROFILE_SUB_OFF:
        user = query.from_user
        if not user:
            return
        await profile_handlers.set_marketing_opt_in(
            users_lock=_users_lock,
            load_users=_load_users,
            save_users=_save_users,
            user_id=user.id,
            value=False,
            source="profile",
            action=data,
        )
        opt_in = user_registry.has_current_marketing_consent(_load_users(), user.id)
        await _edit_or_reply(
            msg,
            profile_handlers.subscriptions_message(marketing_opt_in=opt_in),
            ui.get_profile_subs_keyboard(marketing_opt_in=opt_in),
        )
        await context.bot.send_message(
            chat_id=user.id,
            text=ui.MSG_MARKETING_OFF,
            parse_mode="HTML",
            reply_markup=_main_keyboard_for(user.id),
        )
        return
    if data == ui.CB_POLICY:
        user = query.from_user
        users = _load_users()
        with_marketing = bool(user and user_registry.has_current_policy(users, user.id))
        await _edit_or_reply(
            msg,
            ui.MSG_POLICY_FULL,
            ui.get_policy_keyboard(with_marketing=with_marketing),
            disable_web_page_preview=True,
        )
        return



async def _notify_duplicate_vip(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    username: Optional[str],
    code: str,
    owner: Dict[str, Any],
) -> None:
    orig_id = int(owner.get("user_id") or 0)
    await admin_alerts.notify_duplicate_vip_code(
        context.bot,
        SEED_ADMIN_IDS,
        code=code,
        original_user_id=orig_id,
        original_username=owner.get("username"),
        original_used_at=str(owner.get("used_at") or ""),
        attempter_id=user_id,
        attempter_username=username,
    )
    await inbox_mod.add_entry(
        entry_type="duplicate_vip_code",
        user_id=user_id,
        username=username,
        text=code,
        meta={"original_user_id": orig_id, "original_username": owner.get("username")},
    )


async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mc = update.my_chat_member
    if not mc or not mc.chat or mc.chat.type != "private":
        return
    user = mc.new_chat_member.user
    if not user or user.is_bot:
        return
    status = mc.new_chat_member.status
    async with _users_lock:
        users = _load_users()
        if status in ("kicked", "banned"):
            user_registry.set_bot_status(users, user.id, "blocked")
        elif status == "member":
            user_registry.set_bot_status(users, user.id, "active")
        _save_users(users)


def _format_report_text(*, title: str, st: Dict[str, int], inbox_total: int) -> str:
    top = analytics.top_sections(7)
    top_lines = ", ".join(f"{k}: {v}" for k, v in top) if top else "—"
    return (
        f"<b>{title}</b>\n"
        f"Пользователи (real): {st['real']} (+{st['new_7d']} за 7д)\n"
        f"Активные 7/30д: {st['active_7']} / {st['active_30']}\n"
        f"Спящие (&gt;30д): {st['sleeping']}\n"
        f"VIP: {st['vip']} · без ПДн: {st['no_policy']} · рассылка: {st['marketing']}\n"
        f"Inbox (90д): {inbox_total}\n"
        f"Топ разделов 7д: {top_lines}"
    )


async def _send_weekly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    st = _user_stats()
    inbox_total, _ = await inbox_mod.stats()
    await analytics.rollup_weekly()
    text = _format_report_text(title="📊 Отчёт за неделю", st=st, inbox_total=inbox_total)
    await admin_alerts.notify_seed_admins(context.bot, SEED_ADMIN_IDS, text)


async def _send_monthly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    st = _user_stats()
    inbox_total, _ = await inbox_mod.stats()
    now_msk = datetime.now(user_registry.MSK)
    prev = (now_msk.replace(day=1) - timedelta(days=1))
    await analytics.rollup_monthly(prev.year, prev.month)
    text = _format_report_text(
        title=f"📅 Итог месяца {prev.strftime('%m.%Y')}",
        st=st,
        inbox_total=inbox_total,
    )
    await admin_alerts.notify_seed_admins(context.bot, SEED_ADMIN_IDS, text)


def _schedule_reports(application: Application) -> None:
    if not application.job_queue:
        print("JobQueue недоступен — отчёты не запланированы (установите python-telegram-bot[job-queue])")
        return
    report_time = dt_time(hour=12, minute=0, tzinfo=user_registry.MSK)
    application.job_queue.run_daily(
        _send_weekly_report,
        time=report_time,
        days=(6,),
        name="weekly_report",
    )

    async def monthly_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        now_msk = datetime.now(user_registry.MSK)
        if now_msk.day == 1:
            await _send_monthly_report(ctx)

    application.job_queue.run_daily(
        monthly_job,
        time=report_time,
        name="monthly_report",
    )
    print("Отчёты: воскресенье и 1-е число, 12:00 MSK")


async def _continue_pending_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pending: str,
) -> None:
    """Продолжить действие, которое вызвало gate политики."""
    if pending == "today":
        await show_today(update, context)
    elif pending == "start":
        await start(update, context)
    elif pending == "vip":
        await show_vip(update, context)
    elif pending == "store":
        await show_store(update, context)
    elif pending == "more":
        await show_more(update, context)
    elif pending == "marketing_subscribe":
        user = update.effective_user
        if user and update.effective_message:
            users = _load_users()
            opt_in = user_registry.has_current_marketing_consent(users, user.id)
            text = ui.MSG_MARKETING_ON if opt_in else ui.MSG_MARKETING_OFF
            await update.effective_message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=_main_keyboard_for(user.id),
            )
    elif pending == "weather":
        await weather(update, context)
    elif pending == "moon":
        await moon_cmd(update, context)
    elif pending == "angel":
        key = context.user_data.pop("pending_angel_key", None)
        if key:
            await reply_angelic_sign(update, context, key)
    elif pending == "unknown":
        if update.effective_message:
            await update.effective_message.reply_text(
                ui.UNKNOWN_COMMAND_HINT,
                parse_mode="HTML",
                reply_markup=_main_keyboard_for(update.effective_user.id)
                if update.effective_user
                else ui.get_main_keyboard(),
            )


async def consent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query and query.data == ui.CB_MARKETING_UNSUB:
        await query.answer()
        user = query.from_user
        if user:
            async with _users_lock:
                users = _load_users()
                user_registry.set_marketing_opt_in(
                    users, user.id, False, action=ui.CB_MARKETING_UNSUB
                )
                _save_users(users)
            await consent_log.append(
                user_id=user.id,
                event="marketing_opt_out",
                value=False,
                purpose="telegram_marketing",
                document="marketing-consent",
                document_url=ui.URL_MARKETING_CONSENT,
                policy_version=user_registry.MARKETING_CONSENT_VERSION,
                action=ui.CB_MARKETING_UNSUB,
                source="unsub_button",
                meta={
                    "privacy_policy_version": user_registry.PRIVACY_POLICY_VERSION,
                    "user_agreement_version": user_registry.USER_AGREEMENT_VERSION,
                },
            )
            if query.message:
                await query.message.reply_text(
                    ui.MSG_MARKETING_OFF,
                    parse_mode="HTML",
                    reply_markup=_main_keyboard_for(user.id),
                )
        return
    pending = await consent_handlers.consent_callback(
        update,
        context,
        users_lock=_users_lock,
        load_users=_load_users,
        save_users=_save_users,
        seed_admin_ids=SEED_ADMIN_IDS,
    )
    if pending:
        await _continue_pending_action(update, context, pending)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений и кнопок главного меню."""
    user = update.effective_user
    if not user or not update.message:
        return

    text = (update.message.text or "").strip()
    if _is_seed_admin(user.id):
        await add_admin(user.id)

    if text == ui.BTN_TODAY:
        await show_today(update, context)
        return
    if text == ui.BTN_VIP:
        await show_vip(update, context)
        return
    if text == ui.BTN_STORE:
        await show_store(update, context)
        return
    if text == ui.BTN_MORE:
        await show_more(update, context)
        return
    if text == ui.BTN_MARKETING_ON:
        async with _users_lock:
            users = _load_users()
            if user_registry.is_admin_blocked(users, user.id):
                await update.message.reply_text(ui.MSG_ACCESS_RESTRICTED, parse_mode="HTML")
                return
            if not user_registry.has_current_policy(users, user.id):
                context.user_data["pending_action"] = "marketing_subscribe"
                await consent_handlers.show_policy_gate(update, context)
                return
        context.user_data["pending_action"] = "marketing_subscribe"
        await consent_handlers.show_marketing_offer(update)
        return
    if text.casefold() in (
        "новое место",
        ui.BTN_WEATHER_NEW_LOC.casefold(),
        ui.BTN_WEATHER_NEW_LOC_LEGACY.casefold(),
    ):
        await weather_ask_new_location(update, context)
        return
    if text in (
        ui.BTN_WEATHER_SHARE_LOC,
        ui.BTN_WEATHER_SHARE_LOC_LEGACY,
        "Отправить мою геопозицию",
    ):
        await _weather_ask_location(update.effective_message)
        return

    if _is_seed_admin(user.id):
        if text.casefold() == ui.GOD_TEXT_TRIGGER:
            await _open_god_panel(update)
            return
        admin_angel = _is_admin_unknown_angels_cmd(text)
        if admin_angel:
            await admin_inbox(update, admin_angel)
            return
        admin_mode = context.user_data.get("admin_mode")
        if admin_mode == "user_lookup":
            context.user_data.pop("admin_mode", None)
            await _admin_find_user(update, context, text)
            return
        if admin_mode == "user_action_reason":
            await _admin_receive_selected_user_reason(update, context, text)
            return
        if admin_mode == "vip_add":
            await _admin_prepare_vip_codes(update, context, text)
            return
        if admin_mode == "vip_codes_reason":
            await _admin_receive_batch_reason(update, context, text)
            return
        if admin_mode == "vip_import":
            await _admin_prepare_vip_import(update, context, text)
            return
        if admin_mode == "vip_import_reason":
            await _admin_receive_batch_reason(update, context, text)
            return
        if await _admin_apply_user_cmd(update, context, text):
            return

    if not _is_seed_admin(user.id):
        async with _users_lock:
            if user_registry.is_admin_blocked(_load_users(), user.id):
                await update.message.reply_text(ui.MSG_ACCESS_RESTRICTED, parse_mode="HTML")
                return

    if await vip_handlers.try_vip_code(
        update,
        context,
        text,
        is_vip=_user_is_vip,
        grant_vip=_grant_vip_user,
        show_vip_home=show_vip_home,
        notify_wrong=lambda ctx, **kw: vip_handlers.notify_admin_wrong_code(
            ctx,
            notify_path=VIP_NOTIFY_FILE,
            notify_lock=_vip_notify_lock,
            seed_admin_ids=SEED_ADMIN_IDS,
            load_admins=_load_admins,
            **kw,
        ),
        notify_duplicate=lambda ctx, **kw: _notify_duplicate_vip(ctx, **kw),
        protect_kwargs=PROTECT_KWARGS,
        main_keyboard_for=_main_keyboard_for,
    ):
        return

    if ewasml_services.is_angelic_input(text):
        await reply_angelic_sign(update, context, text)
        return

    if not await _require_access(update, context, "unknown"):
        return

    await ensure_user_saved(update, bot=context.bot)

    entry = await inbox_mod.add_entry(
        entry_type="unknown_command",
        user_id=user.id,
        username=user.username,
        text=text,
    )
    if not entry.get("admin_notified_at"):
        sent = await admin_alerts.notify_inbox_entry(
            context.bot,
            SEED_ADMIN_IDS,
            user_id=user.id,
            username=user.username,
            entry_type="unknown_command",
            text=text,
        )
        if sent:
            await inbox_mod.mark_notified(entry["id"])

    await update.message.reply_text(
        ui.UNKNOWN_COMMAND_HINT,
        parse_mode="HTML",
        reply_markup=_main_keyboard_for(user.id),
    )


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сообщить пользователю и админу о любой необработанной ошибке."""
    print(f"Handler error: {context.error!r}")
    if update:
        print(f"  update={update!r}")
    message = getattr(update, "effective_message", None)
    if message:
        try:
            user = getattr(update, "effective_user", None)
            kb = _main_keyboard_for(user.id) if user else ui.get_main_keyboard()
            await message.reply_text(ui.MSG_TECHNICAL_ERROR, reply_markup=kb)
        except Exception as exc:
            print(f"Error reply failed: {exc!r}")

    error_kind = "JSON-ошибка" if isinstance(context.error, JsonStorageError) else "Неожиданная ошибка"
    details = html.escape(str(context.error)[:3000])
    alert = f"⚠️ {error_kind} MaraniusBOT:\n<code>{details}</code>"
    for admin_id in SEED_ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, alert, parse_mode="HTML")
        except Exception as exc:
            print(f"Error notify {admin_id}: {exc!r}")


async def _notify_storage_recoveries(context) -> None:
    for event in pop_recovery_events():
        text = (
            "✅ MaraniusBOT автоматически восстановил JSON.\n"
            f"Файл: <code>{event['file']}</code>\n"
            f"Копия: <code>{event['backup']}</code>\n"
            "Повреждённый оригинал сохранён для разбора."
        )
        for admin_id in SEED_ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception as exc:
                print(f"Storage recovery notify {admin_id}: {exc!r}")


async def _maranius_post_init(application: Application) -> None:
    """Сброс webhook (иначе polling конфликтует) и явный лог, какой бот подключён."""
    ensure_seed_admins()
    ensure_seed_vip()
    migrated = await inbox_mod.migrate_legacy_unknown_csv()
    if migrated:
        print(f"Inbox: мигрировано из unknown_angelic.csv — {migrated} записей")
    await application.bot.delete_webhook(drop_pending_updates=True)
    cmd_count = await sync_bot_commands(application.bot)
    me = await application.bot.get_me()
    un = f"@{me.username}" if me.username else "(без username)"
    print(f"Подключён к Telegram: {un}, id={me.id}")
    print(f"Меню команд «☰»: {cmd_count} шт. (default + ru)")
    print(f"Seed-админы: {sorted(SEED_ADMIN_IDS)}")
    print(f"Seed-VIP: {sorted(SEED_ADMIN_IDS)}")
    _schedule_reports(application)
    await _notify_storage_recoveries(application)
    if application.job_queue:
        application.job_queue.run_repeating(_notify_storage_recoveries, interval=60, first=60, name="storage_recovery_alerts")


def main() -> None:
    """Запуск бота."""
    global BOT_TOKEN, BOT_PROFILE_ACTIVE

    load_dotenv(_BOT_DIR / ".env", override=True)
    BOT_TOKEN, BOT_PROFILE_ACTIVE = resolve_bot_token()

    if not BOT_TOKEN:
        if BOT_PROFILE_ACTIVE == "test":
            print(
                "Ошибка: BOT_PROFILE=test, но не задан BOT_TOKEN_TEST в .env "
                "(сохрани файл .env и проверь строку BOT_TOKEN_TEST=...)."
            )
        else:
            print(
                "Ошибка: не задан токен. Укажи BOT_TOKEN_PROD или BOT_TOKEN в .env."
            )
        return

    print(f"Профиль бота: {BOT_PROFILE_ACTIVE} (из .env в папке проекта)")
    print(f"Числовой id из токена: {_token_numeric_id(BOT_TOKEN)} (тест и прод должны различаться)")
    if BOT_PROFILE_ACTIVE == "test":
        print(
            "Если появится Conflict: останови все другие окна с bot.py — "
            "с одним токеном может работать только один процесс."
        )

    ensure_seed_admins()
    ensure_seed_vip()
    events_storage.init_storage(BASE_DIR)

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(120.0)
        .write_timeout(600.0)
        .media_write_timeout(600.0)
        .post_init(_maranius_post_init)
    )
    if TELEGRAM_PROXY_URL:
        print(f"Telegram proxy: {TELEGRAM_PROXY_URL}")
        builder = builder.proxy(TELEGRAM_PROXY_URL).get_updates_proxy(TELEGRAM_PROXY_URL)
    application = builder.build()

    application.add_error_handler(_on_error)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("today", today_cmd))
    application.add_handler(CommandHandler("vip", vip_cmd))
    application.add_handler(CommandHandler("store", store_cmd))
    application.add_handler(CommandHandler("contact", contact_cmd))
    application.add_handler(CommandHandler("learning", learning_cmd))
    application.add_handler(CommandHandler("info", info_cmd))
    application.add_handler(CommandHandler("moon", moon_cmd))
    application.add_handler(CommandHandler("policy", policy_cmd))
    application.add_handler(CommandHandler("god", god_cmd))

    application.add_handler(MessageHandler(filters.LOCATION, weather_by_location))
    application.add_handler(
        CallbackQueryHandler(consent_callback, pattern=r"^consent:")
    )
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin:"))
    application.add_handler(
        CallbackQueryHandler(vip_approve_callback, pattern=r"^vip:(approve|reject):")
    )
    application.add_handler(
        CallbackQueryHandler(vip_callback, pattern=r"^vip:(welcome|decks|deck:|sec:|pdf:)")
    )
    application.add_handler(CallbackQueryHandler(today_callback, pattern=r"^today:"))
    application.add_handler(CallbackQueryHandler(more_callback, pattern=r"^more:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    from telegram.ext import ChatMemberHandler

    application.add_handler(
        ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    subscribe_h = make_subscribe_handler(_load_admins, BASE_DIR)
    unsubscribe_h = make_unsubscribe_handler(_load_admins, BASE_DIR)
    reaction_h = make_reaction_handler(_load_admins, BASE_DIR)
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, subscribe_h))
    application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, unsubscribe_h))
    application.add_handler(MessageReactionHandler(reaction_h))

    print("Бот запущен (long polling)...")
    application.run_polling(
        allowed_updates=["message", "callback_query", "message_reaction", "my_chat_member"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
