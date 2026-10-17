"""Уведомления seed-админу (только SEED_ADMIN_IDS)."""

from __future__ import annotations

import asyncio
import html
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Deque, Dict, Optional, Set

from telegram import Bot
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from integrations.diagnostics import log_error

_BASE = Path(__file__).resolve().parent.parent
_RATE_FILE = _BASE / "data" / "admin_notify_rate.json"

# (user_id, minute_bucket) -> count
_inbox_rate: Dict[int, Deque[float]] = {}
_INBOX_MAX_PER_MINUTE = 3
_lock = asyncio.Lock()
_rate_minute: Optional[float] = None
_SEND_ATTEMPTS = 3
_MAX_INLINE_RETRY_DELAY = 5


def _minute_bucket() -> float:
    return datetime.now(timezone.utc).timestamp() // 60


def _user_profile_link(user_id: int, first_name: str) -> str:
    """Кликабельное имя для быстрого перехода к Telegram-профилю."""
    label = html.escape(first_name) if first_name else str(user_id)
    return f'<a href="tg://user?id={user_id}">{label}</a>'


async def _can_notify_inbox(user_id: int) -> bool:
    global _rate_minute
    async with _lock:
        now_min = _minute_bucket()
        if now_min != _rate_minute:
            _inbox_rate.clear()
            _rate_minute = now_min
        dq = _inbox_rate.setdefault(user_id, deque(maxlen=_INBOX_MAX_PER_MINUTE))
        while dq and dq[0] < now_min:
            dq.popleft()
        if len(dq) >= _INBOX_MAX_PER_MINUTE:
            return False
        dq.append(now_min)
        return True


async def _send_admin_message(bot: Bot, admin_id: int, text: str, parse_mode: str) -> bool:
    """Bounded retries for temporary Telegram failures, without logging payloads."""
    for attempt in range(1, _SEND_ATTEMPTS + 1):
        try:
            await bot.send_message(
                chat_id=admin_id, text=text, parse_mode=parse_mode,
                connect_timeout=10, read_timeout=10, write_timeout=10, pool_timeout=5,
            )
            return True
        except (BadRequest, Forbidden) as exc:
            log_error("admin_delivery_rejected", exc, attempt=attempt)
            return False
        except RetryAfter as exc:
            retry = exc.retry_after
            delay = retry.total_seconds() if isinstance(retry, timedelta) else float(retry)
            log_error("admin_delivery_rate_limited", exc, attempt=attempt)
        except NetworkError as exc:
            delay = float(attempt)
            log_error("admin_delivery_network_error", exc, attempt=attempt)
        except Exception as exc:
            log_error("admin_delivery_failed", exc, attempt=attempt)
            return False
        if attempt == _SEND_ATTEMPTS or delay > _MAX_INLINE_RETRY_DELAY:
            return False
        # A timeout can occur after Telegram accepted the message. A retry may
        # duplicate it; sendMessage offers no delivery idempotency key.
        await asyncio.sleep(max(1, delay))
    return False


async def notify_seed_admins(
    bot: Bot,
    seed_admin_ids: Set[int],
    text: str,
    *,
    parse_mode: str = "HTML",
) -> bool:
    recipients = sorted(admin_id for admin_id in seed_admin_ids if admin_id > 0)
    delivered = bool(recipients)
    for admin_id in recipients:
        if not await _send_admin_message(bot, admin_id, text, parse_mode):
            delivered = False
    return delivered


async def notify_new_subscriber(
    bot: Bot,
    seed_admin_ids: Set[int],
    *,
    user_id: int,
    username: Optional[str],
    first_name: str,
) -> None:
    un = f"@{html.escape(username)}" if username else "—"
    name = html.escape(first_name) if first_name else "—"
    text = (
        "🆕 <b>Новый подписчик</b>\n"
        f"ID: <code>{user_id}</code>\n"
        f"Username: {un}\n"
        f"Имя: {name}"
    )
    await notify_seed_admins(bot, seed_admin_ids, text)


async def notify_vip_code_redeemed(
    bot: Bot,
    seed_admin_ids: Set[int],
    *,
    user_id: int,
    username: Optional[str],
    first_name: str,
    code: str,
) -> None:
    """Сообщить seed-админам об успешной активации VIP-кода."""
    un = f"@{html.escape(username)}" if username else "—"
    name = html.escape(first_name) if first_name else "—"
    text = (
        "✅ <b>Новый VIP-пользователь</b>\n"
        f"ID: <code>{user_id}</code>\n"
        f"Username: {un}\n"
        f"Имя: {name}\n"
        f"Введённый VIP-код: <code>{html.escape(code.strip()[:120])}</code>"
    )
    await notify_seed_admins(bot, seed_admin_ids, text)


async def notify_legacy_contact_return(
    bot: Bot,
    seed_admin_ids: Set[int],
    *,
    user_id: int,
    username: Optional[str],
    first_name: str,
    source: str,
) -> None:
    """Notify once when a legacy-inactive contact returns after consent."""
    un = f"@{html.escape(username)}" if username else "—"
    name = _user_profile_link(user_id, first_name)
    text = (
        "↩️ <b>Вернулся пользователь из отдельного списка</b>\n"
        f"ID: <code>{user_id}</code>\n"
        f"Username: {un}\n"
        f"Имя: {name}\n"
        f"Источник: {html.escape(source[:120])}\n\n"
        "Профиль активирован после принятия обязательных документов."
    )
    await notify_seed_admins(bot, seed_admin_ids, text)


async def notify_inbox_entry(
    bot: Bot,
    seed_admin_ids: Set[int],
    *,
    user_id: int,
    username: Optional[str],
    entry_type: str,
    text: str,
) -> bool:
    if not await _can_notify_inbox(user_id):
        return False
    un = f"@{html.escape(username)}" if username else "—"
    body = (
        "📥 <b>Inbox</b>\n"
        f"Тип: {html.escape(entry_type)}\n"
        f"User: <code>{user_id}</code> {un}\n"
        f"Текст: <code>{html.escape(text[:500])}</code>"
    )
    return await notify_seed_admins(bot, seed_admin_ids, body)


async def notify_duplicate_vip_code(
    bot: Bot,
    seed_admin_ids: Set[int],
    *,
    code: str,
    original_user_id: int,
    original_username: Optional[str],
    original_used_at: str,
    attempter_id: int,
    attempter_username: Optional[str],
) -> None:
    ou = f"@{html.escape(original_username)}" if original_username else "—"
    au = f"@{html.escape(attempter_username)}" if attempter_username else "—"
    text = (
        "⚠️ <b>Повтор VIP-кода</b>\n"
        f"Код: <code>{html.escape(code[:80])}</code>\n\n"
        f"<b>Первый активатор</b>\n"
        f"ID: <code>{original_user_id}</code> {ou}\n"
        f"Когда: {html.escape(original_used_at)}\n\n"
        f"<b>Повторный ввод</b>\n"
        f"ID: <code>{attempter_id}</code> {au}"
    )
    await notify_seed_admins(bot, seed_admin_ids, text)
