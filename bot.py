import asyncio
import html
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from dotenv import load_dotenv
from telegram import MenuButtonCommands, Update
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
    admin_alerts,
    business_events,
    analytics,
    daily_practice,
    dice_content,
    ewasml_services,
    inbox as inbox_mod,
    legacy_contacts,
    platform_db,
    notification_outbox,
    runtime_health,
    referrals,
    user_registry,
    vip_codes,
    vip_content,
)
from integrations.json_storage import JsonStorageError, load_json, pop_recovery_events, save_json, run_storage_io, atomic_json
from integrations.diagnostics import log_error
from events.handlers import (
    make_subscribe_handler,
    make_unsubscribe_handler,
    make_reaction_handler,
)
from events import storage as events_storage

import ui
from technical_heartbeat import heartbeat_loop
from handlers import admin as admin_handlers
from handlers import weather as weather_handlers
from handlers import reports as report_handlers
from handlers import consent as consent_handlers
from handlers import profile as profile_handlers
from handlers import vip as vip_handlers

# Суперюзеры стенда — режим бога и VIP навсегда без кода в чат.
SEED_ADMIN_IDS: Set[int] = {186758977}
# Реферальная механика сохранена как черновик, но до решения по её условиям
# не создаёт привязки, начисления или заявки из пользовательского интерфейса.
REFERRAL_PROGRAM_ENABLED = False


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


async def ensure_user_saved(update: Update, *, bot=None, force: bool = False) -> bool:
    """
    Обновить/добавить данные пользователя в users.json.
    Returns True если пользователь новый (первый визит).
    Без force профиль не пишется до принятия пользовательского соглашения
    и актуального согласия на ПДн.
    """
    user = update.effective_user
    if not user:
        return False
    async with _users_lock:
        permitted, is_new = await run_storage_io(
            business_events.profile_visit, user, load_users=_load_users,
            save_users=_save_users, seed_admin_ids=SEED_ADMIN_IDS,
            force=force, notify=bool(bot),
        )
    if not permitted:
        return False

    name = (user.first_name or "") + (" " + (user.last_name or "") if user.last_name else "")
    if not name.strip():
        name = user.username or None
    await platform_db.get_or_create_user(
        telegram_id=user.id,
        name=name.strip() or None,
    )

    if REFERRAL_PROGRAM_ENABLED:
        # Квалификация реферала идёт отдельно от users.json и не зависит от
        # количества нажатий: учитываем только новый активный календарный день.
        await referrals.record_activity(
            user.id,
            await run_storage_io(_load_users),
            excluded_ids=SEED_ADMIN_IDS,
        )

    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        _known_chats.add(chat.id)

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


async def _require_private_chat(update: Update) -> bool:
    """Закрытые материалы и административные данные доступны только в личке."""
    chat = update.effective_chat
    if chat and chat.type == "private":
        return True
    query = update.callback_query
    if query:
        await query.answer(ui.MSG_PRIVATE_CHAT_ONLY, show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(ui.MSG_PRIVATE_CHAT_ONLY)
    return False


def is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь администратором (файл + seed)."""
    if user_id in SEED_ADMIN_IDS:
        return True
    return user_id in _load_admins()


async def add_admin(user_id: int) -> None:
    """Добавить пользователя в список администраторов."""
    async with _admins_lock:
        admins = await run_storage_io(_load_admins)
        admins.add(user_id)
        await run_storage_io(_save_admins, admins)


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

    payload = " ".join(getattr(context, "args", []) or []).strip()[:200]

    # До согласия не сохраняем Telegram-профиль или параметр deep-link нового
    # пользователя. Параметр /start может быть данными для аналитики и пишется
    # только после отдельного активного согласия.
    # В context.user_data сохраняем только технический payload: после gate
    # Telegram не повторяет args исходного сообщения.
    if payload:
        context.user_data["pending_referral_payload"] = payload
    async with _users_lock:
        users = await run_storage_io(_load_users)
        has_access = user_registry.has_current_access(users, user.id)
    if not has_access:
        context.user_data["pending_action"] = "start"
        await consent_handlers.show_policy_gate(
            update, context, users=users, user_id=user.id
        )
        return

    is_new = await ensure_user_saved(update, bot=context.bot, force=user.id in SEED_ADMIN_IDS)
    payload = context.user_data.pop("pending_referral_payload", payload)
    if payload:
        async with _users_lock:
            users = await run_storage_io(_load_users)
            user_registry.capture_start_param(users, user.id, payload)
            await run_storage_io(_save_users, users)
        # Ссылку можно засчитать только первому визиту нового пользователя.
        if is_new and REFERRAL_PROGRAM_ENABLED:
            await referrals.register_referral(
                user.id,
                payload,
                await run_storage_io(_load_users),
                excluded_ids=SEED_ADMIN_IDS,
            )
            await referrals.record_activity(
                user.id,
                await run_storage_io(_load_users),
                excluded_ids=SEED_ADMIN_IDS,
            )
    async with _users_lock:
        users = await run_storage_io(_load_users)
        if users.get(str(user.id), {}).get("bot_status") == "blocked":
            user_registry.set_bot_status(users, user.id, "active")
            await run_storage_io(_save_users, users)
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
        reply_markup=await run_storage_io(_main_keyboard_for, user.id),
    )


_weather_handlers = weather_handlers.WeatherHandlers(
    users_lock=_users_lock,
    load_users=lambda: _load_users(),
    save_users=lambda users: _save_users(users),
    require_access=lambda update, context, action: _require_access(update, context, action),
    ensure_user_saved=lambda update, **kwargs: ensure_user_saved(update, **kwargs),
    main_keyboard_for=lambda user_id: _main_keyboard_for(user_id),
    edit_or_reply=lambda message, text, keyboard: _edit_or_reply(message, text, keyboard),
)
_reports = report_handlers.ReportHandlers(
    user_stats=lambda: user_registry.stats_summary(_load_users()),
    seed_admin_ids=SEED_ADMIN_IDS,
)


async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _weather_handlers.weather(update, context)


async def weather_by_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _weather_handlers.weather_by_location(update, context)


async def weather_ask_new_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _weather_handlers.weather_ask_new_location(update, context)


async def moon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _weather_handlers.moon_cmd(update, context)


def _schedule_reports(application: Application) -> None:
    _reports._schedule_reports(application)


_admin_handlers = admin_handlers.AdminHandlers(
    seed_admin_ids=SEED_ADMIN_IDS,
    users_lock=_users_lock,
    load_users=lambda: _load_users(),
    save_users=lambda users: _save_users(users),
    require_private_chat=lambda update: _require_private_chat(update),
    ensure_user_saved=lambda update, **kwargs: ensure_user_saved(update, **kwargs),
    add_admin=lambda user_id: add_admin(user_id),
    main_keyboard_for=lambda user_id: _main_keyboard_for(user_id),
    audit_file=ADMIN_AUDIT_FILE,
    consent_file=CONSENT_LOG_FILE,
    bot_profile=lambda: BOT_PROFILE_ACTIVE,
)


async def god_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_handlers.god_cmd(update, context)


async def _admin_guard(update: Update) -> bool:
    return await _admin_handlers._admin_guard(update)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _admin_handlers.admin_callback(update, context)


def _is_seed_admin(user_id: int) -> bool:
    """Проверка только seed-админов (для /god и управления пользователями)."""
    return user_id in SEED_ADMIN_IDS


def _update_from_callback(query, update_id: int) -> Update:
    """Callback: effective_user = нажавший кнопку, не бот."""
    return Update(update_id, callback_query=query)


def _clear_vip_awaiting(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_vip_code", None)


async def _handle_card_pull(message, user_id: int) -> None:
    uid = str(user_id)
    async with _users_lock:
        state = daily_practice.practice_from_user((await run_storage_io(_load_users)).get(uid))
    existing = state.get("card")
    if existing:
        await _edit_or_reply(
            message,
            ui.format_card_already(existing["title"], existing["url"]),
            ui.get_card_hub_back_keyboard(existing["url"]),
        )
        return

    await _edit_or_reply(message, ui.MSG_CARD_SHUFFLE, None)
    await asyncio.sleep(2.5)
    pull = await run_storage_io(daily_practice.pick_random_card)
    if not pull:
        await _edit_or_reply(
            message,
            ui.MSG_CARD_CATALOG_EMPTY,
            ui.get_card_hub_keyboard(),
        )
        return

    async with _users_lock:
        users = await run_storage_io(_load_users)
        state = daily_practice.practice_from_user(users.get(uid))
        concurrent_existing = state.get("card")
        if not concurrent_existing:
            users.setdefault(uid, {"id": user_id})
            users[uid]["daily_practice"] = daily_practice.apply_card_pull(state, pull)
            await run_storage_io(_save_users, users)
    if concurrent_existing:
        await _edit_or_reply(message, ui.format_card_already(concurrent_existing["title"], concurrent_existing["url"]), ui.get_card_hub_back_keyboard(concurrent_existing["url"]))
        return

    await _edit_or_reply(
        message,
        ui.format_card_success(pull["title"], pull["url"]),
        ui.get_card_hub_back_keyboard(pull["url"]),
    )


async def _handle_crystal_pull(message, user_id: int) -> None:
    uid = str(user_id)
    async with _users_lock:
        state = daily_practice.practice_from_user((await run_storage_io(_load_users)).get(uid))
    existing = state.get("crystal")
    if existing:
        await _edit_or_reply(
            message,
            ui.format_crystal_already(existing["title"], existing["url"]),
            ui.get_card_hub_back_keyboard(existing["url"]),
        )
        return

    await _edit_or_reply(message, ui.MSG_CRYSTAL_SHUFFLE, None)
    await asyncio.sleep(2.5)
    pull = await run_storage_io(daily_practice.pick_random_crystal)
    if not pull:
        await _edit_or_reply(
            message,
            ui.MSG_CRYSTAL_CATALOG_EMPTY,
            ui.get_card_hub_keyboard(),
        )
        return

    async with _users_lock:
        users = await run_storage_io(_load_users)
        state = daily_practice.practice_from_user(users.get(uid))
        concurrent_existing = state.get("crystal")
        if not concurrent_existing:
            users.setdefault(uid, {"id": user_id})
            users[uid]["daily_practice"] = daily_practice.apply_crystal_pull(state, pull)
            await run_storage_io(_save_users, users)
    if concurrent_existing:
        await _edit_or_reply(message, ui.format_crystal_already(concurrent_existing["title"], concurrent_existing["url"]), ui.get_card_hub_back_keyboard(concurrent_existing["url"]))
        return

    await _edit_or_reply(
        message,
        ui.format_crystal_success(pull["title"], pull["url"]),
        ui.get_card_hub_back_keyboard(pull["url"]),
    )


async def _handle_dice_roll(message, user_id: int, bot) -> None:
    uid = str(user_id)
    async with _users_lock:
        state = daily_practice.practice_from_user((await run_storage_io(_load_users)).get(uid))
    existing = state.get("dice")
    if existing:
        dice_text = await run_storage_io(dice_content.get_dice_message_html, existing["value"])
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
        users = await run_storage_io(_load_users)
        state = daily_practice.practice_from_user(users.get(uid))
        concurrent_existing = state.get("dice")
        if not concurrent_existing:
            users.setdefault(uid, {"id": user_id})
            users[uid]["daily_practice"] = daily_practice.apply_dice_roll(state, value)
            await run_storage_io(_save_users, users)

    if concurrent_existing:
        dice_text = await run_storage_io(dice_content.get_dice_message_html, concurrent_existing["value"])
        await dice_msg.reply_text(ui.format_dice_already(dice_text), parse_mode="HTML")
        return

    dice_text = await run_storage_io(dice_content.get_dice_message_html, value)
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
            reply_markup=await run_storage_io(_main_keyboard_for, uid) if uid else ui.get_main_keyboard(),
            disable_web_page_preview=disable_web_page_preview,
            **extra,
        )


def _user_is_vip(user_id: int) -> bool:
    return user_registry.is_vip(_load_users(), user_id, seed_admin_ids=SEED_ADMIN_IDS)


async def _grant_vip_user(user_id: int, *, source: str = "code") -> None:
    async with _users_lock:
        users = await run_storage_io(_load_users)
        user_registry.grant_vip(users, user_id, source=source)
        await run_storage_io(_save_users, users)


async def _activate_vip_code(raw_code, user):
    async with _users_lock:
        return await run_storage_io(
            business_events.activate_vip_code, raw_code, user,
            load_users=_load_users, save_users=_save_users, seed_admin_ids=SEED_ADMIN_IDS,
        )


async def show_vip_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """VIP: приветствие или запрос кода."""
    if not await _require_private_chat(update):
        return
    if not await _require_access(update, context, "vip"):
        return
    user = update.effective_user
    if not user:
        return
    if await run_storage_io(_user_is_vip, user.id):
        context.user_data.pop("awaiting_vip_code", None)
        await _reply_screen(
            update,
            await run_storage_io(vip_content.vip_home_html),
            inline_markup=await run_storage_io(vip_content.deck_menu_keyboard),
            protect=True,
        )
        return
    context.user_data["awaiting_vip_code"] = True
    text = f"<b>{ui.BTN_VIP}</b>\n\n{await run_storage_io(vip_content.no_access_html)}"
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
    users = await run_storage_io(_load_users)
    user = update.effective_user
    with_marketing = bool(user and user_registry.has_current_access(users, user.id))
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


async def reply_angelic_sign(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Ответ расшифровкой ангельского знака из локальных CSV."""
    message = update.effective_message
    if not message:
        return
    context.user_data["pending_angel_key"] = key
    if not await _require_access(update, context, "angel"):
        return
    context.user_data.pop("pending_angel_key", None)
    await ensure_user_saved(update, bot=context.bot)
    meaning, normalized = await run_storage_io(ewasml_services.lookup_angelic_sign, key)
    if meaning:
        display = normalized if normalized != key.strip() else key.strip()
        text = ui.format_angelic_sign(display, meaning)
        await message.reply_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=await run_storage_io(_main_keyboard_for, update.effective_user.id)
            if update.effective_user
            else ui.get_main_keyboard(),
            **PROTECT_KWARGS,
        )
        return

    user = update.effective_user
    if user:
        await business_events.add_inbox_entry(
            SEED_ADMIN_IDS, entry_type="unknown_angel", user_id=user.id,
            username=user.username, text=key.strip(), meta={"normalized": normalized},
        )
    await message.reply_text(
        ui.MSG_ANGEL_NOT_FOUND.format(sign=key.strip()),
        parse_mode="HTML",
        reply_markup=await run_storage_io(_main_keyboard_for, user.id) if user else ui.get_main_keyboard(),
        **PROTECT_KWARGS,
    )


async def vip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    if not await _require_private_chat(update):
        return
    if not await _require_access(update, context, "vip"):
        await query.answer()
        return
    await vip_handlers.vip_menu_callback(
        update,
        context,
        is_vip=_user_is_vip,
        protect_kwargs=PROTECT_KWARGS,
    )


async def vip_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def commit_decision(action, uid, invalid_code=""):
        def commit():
            if action == "vip_grant_from_alert":
                users = _load_users()
                user_registry.grant_vip(users, uid, source="admin_grant")
                _save_users(users)
                if invalid_code:
                    vip_codes._mark_code_used_sync(invalid_code, user_id=uid)
            _admin_handlers._audit_admin_action_sync(
                update, action=action, target_ids=[uid],
                reason="Решение по алерту о неверном VIP-коде",
                meta={"source": "vip_alert", "invalid_code": invalid_code or None},
            )
        async with _users_lock:
            await run_storage_io(atomic_json, commit)

    await vip_handlers.vip_approve_callback(
        update,
        context,
        admin_guard=_admin_guard,
        grant_vip=_grant_vip_user,
        main_keyboard_for=_main_keyboard_for,
        commit_decision=commit_decision,
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


async def _render_referral_dashboard(message, user_id: int, bot, *, notice: str = "") -> None:
    """Показать реферальный экран с актуальным Telegram deep-link."""
    me = await bot.get_me()
    dashboard = await referrals.get_dashboard(
        user_id,
        await run_storage_io(_load_users),
        me.username or "MaraniusBOT",
    )
    await _edit_or_reply(
        message,
        ui.format_referral_dashboard_html(dashboard, notice=notice),
        ui.get_referral_keyboard(dashboard["rewards"]),
        disable_web_page_preview=True,
    )


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
    if data == ui.CB_SOCIAL_NETWORKS:
        await _edit_or_reply(
            msg,
            ui.MSG_SOCIAL_NETWORKS_STUB,
            ui.get_social_networks_keyboard(),
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
    if data == ui.CB_PROFILE_REFERRAL:
        await _edit_or_reply(
            msg,
            ui.MSG_REFERRAL_IN_PROGRESS,
            ui.get_referral_in_progress_keyboard(),
        )
        return
    if data.startswith(ui.CB_PROFILE_REFERRAL_REDEEM_PREFIX):
        # Старые inline-кнопки, если они остались в чате, тоже не должны
        # списывать баллы или создавать заявки до запуска программы.
        await _edit_or_reply(
            msg,
            ui.MSG_REFERRAL_IN_PROGRESS,
            ui.get_referral_in_progress_keyboard(),
        )
        return
    if data == ui.CB_PROFILE_STATUS:
        user = query.from_user
        if not user:
            return
        row = user_registry.get_user(await run_storage_io(_load_users), user.id)
        courses = await platform_db.get_user_courses(str(user.id))
        text = profile_handlers.format_status_html(
            row,
            is_vip=await run_storage_io(_user_is_vip, user.id),
            courses=courses,
        )
        await _edit_or_reply(msg, text, ui.get_profile_status_back_keyboard())
        return
    if data == ui.CB_PROFILE_SUBS:
        user = query.from_user
        if not user:
            return
        users = await run_storage_io(_load_users)
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
        opt_in = user_registry.has_current_marketing_consent(await run_storage_io(_load_users), user.id)
        await _edit_or_reply(
            msg,
            profile_handlers.subscriptions_message(marketing_opt_in=opt_in),
            ui.get_profile_subs_keyboard(marketing_opt_in=opt_in),
        )
        await context.bot.send_message(
            chat_id=user.id,
            text=ui.MSG_MARKETING_OFF,
            parse_mode="HTML",
            reply_markup=await run_storage_io(_main_keyboard_for, user.id),
        )
        return
    if data == ui.CB_POLICY:
        user = query.from_user
        users = await run_storage_io(_load_users)
        with_marketing = bool(user and user_registry.has_current_access(users, user.id))
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


async def _notify_vip_code_redeemed(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    username: Optional[str],
    first_name: str,
    code: str,
) -> None:
    await admin_alerts.notify_vip_code_redeemed(
        context.bot,
        SEED_ADMIN_IDS,
        user_id=user_id,
        username=username,
        first_name=first_name,
        code=code,
    )


async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mc = update.my_chat_member
    if not mc or not mc.chat or mc.chat.type != "private":
        return
    # У MY_CHAT_MEMBER new_chat_member.user — сам бот. В личном чате
    # владельца профиля определяет chat.id, а не участник с новым статусом.
    user_id = mc.chat.id
    status = mc.new_chat_member.status
    if status not in ("kicked", "banned", "member"):
        return
    if mc.old_chat_member.status == status:
        return
    async with _users_lock:
        users = await run_storage_io(_load_users)
        # Не создаём профиль по служебному событию до принятия документов.
        if str(user_id) not in users:
            return
        if status in ("kicked", "banned"):
            user_registry.set_bot_status(users, user_id, "blocked")
        elif status == "member":
            user_registry.set_bot_status(users, user_id, "active")
        await run_storage_io(_save_users, users)


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
    elif pending == "contact":
        await show_contact(update, context)
    elif pending == "services":
        await show_services(update, context)
    elif pending == "learning":
        await show_learning(update, context)
    elif pending == "info":
        await show_info(update, context)
    elif pending == "marketing_subscribe":
        user = update.effective_user
        if user and update.effective_message:
            users = await run_storage_io(_load_users)
            opt_in = user_registry.has_current_marketing_consent(users, user.id)
            text = ui.MSG_MARKETING_ON if opt_in else ui.MSG_MARKETING_OFF
            await update.effective_message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=await run_storage_io(_main_keyboard_for, user.id),
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
                reply_markup=await run_storage_io(_main_keyboard_for, update.effective_user.id)
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
                await run_storage_io(
                    consent_handlers.update_marketing, _load_users, _save_users, user.id, False,
                    source="unsub_button", action=ui.CB_MARKETING_UNSUB, require_access=False,
                )
            if query.message:
                await query.message.reply_text(
                    ui.MSG_MARKETING_OFF,
                    parse_mode="HTML",
                    reply_markup=await run_storage_io(_main_keyboard_for, user.id),
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
        if not await _require_private_chat(update):
            return
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
            users = await run_storage_io(_load_users)
            if user_registry.is_admin_blocked(users, user.id):
                await update.message.reply_text(ui.MSG_ACCESS_RESTRICTED, parse_mode="HTML")
                return
            if not user_registry.has_current_access(users, user.id):
                context.user_data["pending_action"] = "marketing_subscribe"
                await consent_handlers.show_policy_gate(
                    update, context, users=users, user_id=user.id
                )
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
        await weather_handlers._weather_ask_location(update.effective_message)
        return

    if await _admin_handlers.handle_text(update, context, text):
        return

    if not _is_seed_admin(user.id):
        async with _users_lock:
            if user_registry.is_admin_blocked(await run_storage_io(_load_users), user.id):
                await update.message.reply_text(ui.MSG_ACCESS_RESTRICTED, parse_mode="HTML")
                return

    if context.user_data.get("awaiting_vip_code") and not await _require_private_chat(update):
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
        notify_success=lambda ctx, **kw: _notify_vip_code_redeemed(ctx, **kw),
        protect_kwargs=PROTECT_KWARGS,
        main_keyboard_for=_main_keyboard_for,
        activate_code=_activate_vip_code,
    ):
        return

    if ewasml_services.is_angelic_input(text):
        await reply_angelic_sign(update, context, text)
        return

    if not await _require_access(update, context, "unknown"):
        return

    await ensure_user_saved(update, bot=context.bot)

    await business_events.add_inbox_entry(
        SEED_ADMIN_IDS, entry_type="unknown_command", user_id=user.id,
        username=user.username, text=text,
    )

    await update.message.reply_text(
        ui.UNKNOWN_COMMAND_HINT,
        parse_mode="HTML",
        reply_markup=await run_storage_io(_main_keyboard_for, user.id),
    )


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сообщить пользователю и админу о любой необработанной ошибке."""
    log_error("handler_failed", context.error, update_id=getattr(update, "update_id", None))
    message = getattr(update, "effective_message", None)
    if message:
        try:
            user = getattr(update, "effective_user", None)
            kb = await run_storage_io(_main_keyboard_for, user.id) if user else ui.get_main_keyboard()
            await message.reply_text(ui.MSG_TECHNICAL_ERROR, reply_markup=kb)
        except Exception as exc:
            log_error("error_reply_failed", exc)

    error_kind = "JSON-ошибка" if isinstance(context.error, JsonStorageError) else "Неожиданная ошибка"
    details = html.escape(type(context.error).__name__)
    alert = f"⚠️ {error_kind} MaraniusBOT:\n<code>{details}</code>"
    for admin_id in SEED_ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, alert, parse_mode="HTML")
        except Exception as exc:
            log_error("error_notification_failed", exc)


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
                log_error("storage_recovery_notification_failed", exc)


def _apply_pending_legacy_migration() -> Optional[Dict[str, Any]]:
    """Consume a staged legacy roster before polling starts.

    The import runs in the bot process, before Telegram updates are handled, so
    it cannot race with a normal users.json update.  The staged ID-only
    manifest is retained together with its result and then removed from the
    pending path to make the operation one-time.
    """
    pending_path = legacy_contacts.PENDING_MIGRATION_FILE
    if not pending_path.is_file():
        return None

    manifest = load_json(pending_path, None, allow_recovery=False)
    if not isinstance(manifest, dict):
        raise ValueError("Legacy migration manifest must be an object")
    required_keys = ("source", "active_user_ids", "vip_user_ids", "inactive_user_ids")
    if any(key not in manifest for key in required_keys):
        raise ValueError("Legacy migration manifest is missing required fields")

    def commit():
        previous = load_json(legacy_contacts.LAST_MIGRATION_FILE, None, allow_recovery=False)
        if isinstance(previous, dict) and previous.get("manifest") == manifest:
            return None
        users = _load_users()
        inactive_store = legacy_contacts.load_inactive_store()
        result = legacy_contacts.apply_legacy_migration(
            users,
            inactive_store,
            active_user_ids=manifest["active_user_ids"],
            vip_user_ids=manifest["vip_user_ids"],
            inactive_user_ids=manifest["inactive_user_ids"],
            source=str(manifest["source"]),
        )

        legacy_contacts.save_inactive_store(inactive_store)
        _save_users(users)
        completed = {
            "manifest": manifest,
            "result": result,
            "applied_at": user_registry.now_iso(),
        }
        save_json(legacy_contacts.LAST_MIGRATION_FILE, completed, trailing_newline=True)

        notification_outbox._enqueue_sync(
            SEED_ADMIN_IDS,
            (
                "✅ <b>Миграция старой базы завершена</b>\n"
                f"Активный пул: <b>{len(completed['manifest']['active_user_ids'])}</b>\n"
                f"VIP: <b>{len(completed['manifest']['vip_user_ids'])}</b>\n"
                f"Отдельный список: <b>{len(completed['manifest']['inactive_user_ids'])}</b>\n"
                f"Новых активных записей: <b>{result['active_created']}</b>\n"
                f"Выдано VIP: <b>{result['vip_granted']}</b>\n"
                f"Добавлено в отдельный список: <b>{result['inactive_stored']}</b>"
            ),
        )
        return completed
    completed = atomic_json(commit)
    pending_path.unlink()
    return completed


async def _maranius_post_init(application: Application) -> None:
    """Сброс webhook (иначе polling конфликтует) и явный лог, какой бот подключён."""
    await run_storage_io(ensure_seed_admins)
    await run_storage_io(ensure_seed_vip)
    await run_storage_io(_apply_pending_legacy_migration)
    migrated = await inbox_mod.migrate_legacy_unknown_csv()
    if migrated:
        print(f"Inbox: мигрировано из unknown_angelic.csv — {migrated} записей")
    await application.bot.delete_webhook(drop_pending_updates=False)
    cmd_count = await sync_bot_commands(application.bot)
    me = await application.bot.get_me()
    un = f"@{me.username}" if me.username else "(без username)"
    print(f"Подключён к Telegram: {un}, id={me.id}")
    print(f"Меню команд «☰»: {cmd_count} шт. (default + ru)")
    print(f"Seed-админы: {sorted(SEED_ADMIN_IDS)}")
    print(f"Seed-VIP: {sorted(SEED_ADMIN_IDS)}")
    _schedule_reports(application)
    health = runtime_health.RuntimeHealth()
    application.bot_data["runtime_health"] = health
    application.bot_data["technical_heartbeat_task"] = asyncio.create_task(
        heartbeat_loop(application.bot, SEED_ADMIN_IDS, "Maranius", progress=health.progress["heartbeat"].beat),
        name="technical-heartbeat",
    )
    application.bot_data["notification_outbox_task"] = asyncio.create_task(
        notification_outbox.worker_loop(application.bot, SEED_ADMIN_IDS, watchdog=health.progress["outbox"]), name="notification-outbox",
    )
    print("Очередь уведомлений: фоновая доставка включена")
    await _notify_storage_recoveries(application)
    if application.job_queue:
        application.job_queue.run_repeating(_notify_storage_recoveries, interval=60, first=60, name="storage_recovery_alerts")
        application.job_queue.run_repeating(runtime_health.scheduler_tick, interval=15, first=1, name="runtime_health_tick")
    application.bot_data["runtime_health_task"] = asyncio.create_task(health.run(application), name="runtime-health")
    print("Runtime healthcheck: event loop, задачи, планировщик и хранилище включены")


async def _stop_technical_heartbeat(application: Application) -> None:
    health = application.bot_data.pop("runtime_health", None)
    if health is not None:
        health.stopping = True
    tasks = [application.bot_data.pop(name, None) for name in (
        "technical_heartbeat_task", "notification_outbox_task", "runtime_health_task",
    )]
    for task in tasks:
        if task is not None:
            task.cancel()
    for task in tasks:
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                # One crashed worker must not prevent draining the other tasks.
                log_error("background_task_stopped", exc)
    if health is not None:
        try:
            from integrations.json_storage import finish_storage_operation
            await finish_storage_operation(asyncio.to_thread(
                runtime_health.write_snapshot, health.snapshot(application), health.path,
            ))
        except Exception as exc:
            log_error("runtime_health_shutdown_failed", exc)


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
        .post_stop(_stop_technical_heartbeat)
    )
    if TELEGRAM_PROXY_URL:
        print("Telegram proxy enabled")
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
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
