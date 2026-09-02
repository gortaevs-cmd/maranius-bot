"""Уведомления seed-админу (только SEED_ADMIN_IDS)."""

from __future__ import annotations

import asyncio
import html
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, Optional, Set

from telegram import Bot

_BASE = Path(__file__).resolve().parent.parent
_RATE_FILE = _BASE / "data" / "admin_notify_rate.json"

# (user_id, minute_bucket) -> count
_inbox_rate: Dict[int, Deque[float]] = {}
_INBOX_MAX_PER_MINUTE = 3
_lock = asyncio.Lock()


def _minute_bucket() -> float:
    return datetime.now(timezone.utc).timestamp() // 60


async def _can_notify_inbox(user_id: int) -> bool:
    async with _lock:
        now_min = _minute_bucket()
        dq = _inbox_rate.setdefault(user_id, deque(maxlen=_INBOX_MAX_PER_MINUTE))
        while dq and dq[0] < now_min:
            dq.popleft()
        if len(dq) >= _INBOX_MAX_PER_MINUTE:
            return False
        dq.append(now_min)
        return True


async def notify_seed_admins(
    bot: Bot,
    seed_admin_ids: Set[int],
    text: str,
    *,
    parse_mode: str = "HTML",
) -> None:
    for admin_id in seed_admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode=parse_mode)
        except Exception as exc:
            print(f"admin alert {admin_id}: {exc!r}")


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
    name = html.escape(first_name) if first_name else "—"
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
        f"Текст: {html.escape(text[:500])}"
    )
    await notify_seed_admins(bot, seed_admin_ids, body)
    return True


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
