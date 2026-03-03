"""Отправка уведомлений админам о событиях в чатах."""
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Set

from telegram import Bot


def _user_link(user: Dict[str, Any]) -> str:
    """Ссылка на пользователя."""
    username = user.get("username")
    user_id = user.get("id")
    if username:
        return f"https://t.me/{username}"
    return f"tg://user?id={user_id}" if user_id else "—"


def get_post_link(chat: Dict[str, Any], message_id: int) -> str:
    """Ссылка на пост в чате/канале."""
    username = chat.get("username")
    chat_id = chat.get("id")
    if username:
        return f"https://t.me/{username}/{message_id}"
    # Приватный канал/группа: -1001234567890 -> 1234567890
    if chat_id and chat_id < 0:
        cid = str(chat_id).replace("-100", "")
        return f"https://t.me/c/{cid}/{message_id}"
    return f"msg_id={message_id}"


def _format_datetime(ts: str) -> str:
    """Форматировать timestamp."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ts


def format_unsubscribe_notification(chat: Dict[str, Any], user: Dict[str, Any], ts: str) -> str:
    """Форматировать уведомление об отписке."""
    parts = [
        "👋 <b>Отписка</b>",
        f"Чат/канал: {chat.get('title', '—')}",
        f"Пользователь: {user.get('first_name', '')} {user.get('last_name', '')}".strip() or "—",
        f"Логин: @{user.get('username', '—')}" if user.get('username') else "Логин: —",
        f"ID: {user.get('id', '—')}",
        f"Ссылка: {_user_link(user)}",
        f"Дата: {_format_datetime(ts)}",
    ]
    return "\n".join(parts)


def format_subscribe_notification(chat: Dict[str, Any], user: Dict[str, Any], ts: str) -> str:
    """Форматировать уведомление о подписке."""
    parts = [
        "➕ <b>Подписка</b>",
        f"Чат/канал: {chat.get('title', '—')}",
        f"Пользователь: {user.get('first_name', '')} {user.get('last_name', '')}".strip() or "—",
        f"Логин: @{user.get('username', '—')}" if user.get('username') else "Логин: —",
        f"ID: {user.get('id', '—')}",
        f"Ссылка: {_user_link(user)}",
        f"Дата: {_format_datetime(ts)}",
    ]
    return "\n".join(parts)


def format_reaction_notification(
    chat: Dict[str, Any],
    user: Dict[str, Any],
    message_id: int,
    reaction: str,
    ts: str,
) -> str:
    """Форматировать уведомление о реакции."""
    post_link = get_post_link(chat, message_id)
    parts = [
        "❤️ <b>Реакция</b>",
        f"Чат/канал: {chat.get('title', '—')}",
        f"Пользователь: {user.get('first_name', '')} {user.get('last_name', '')}".strip() or "—",
        f"Логин: @{user.get('username', '—')}" if user.get('username') else "Логин: —",
        f"ID: {user.get('id', '—')}",
        f"Ссылка: {_user_link(user)}",
        f"ID поста: {message_id}",
        f"Ссылка на пост: {post_link}",
        f"Реакция: {reaction}",
        f"Дата: {_format_datetime(ts)}",
    ]
    return "\n".join(parts)


async def notify_admins(
    bot: Bot,
    get_admins: Callable[[], Set[int]],
    text: str,
) -> None:
    """Отправить уведомление всем админам."""
    admins = get_admins()
    if not admins:
        return
    for admin_id in admins:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass  # Игнорируем ошибки отправки (заблокировал бота и т.п.)
