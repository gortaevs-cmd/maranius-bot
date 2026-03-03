"""Обработчики событий: подписка, отписка, реакции."""
from typing import Callable, Set

from telegram import Update
from telegram.ext import ContextTypes

from . import notifications
from . import storage


def _chat_dict(chat) -> dict:
    """Преобразовать Chat в словарь."""
    if not chat:
        return {}
    return {
        "id": chat.id,
        "title": getattr(chat, "title", None) or "",
        "type": getattr(chat, "type", None) or "",
        "username": getattr(chat, "username", None) or "",
    }


def _user_dict(user) -> dict:
    """Преобразовать User в словарь."""
    if not user:
        return {}
    username = user.username or ""
    link = f"https://t.me/{username}" if username else f"tg://user?id={user.id}"
    return {
        "id": user.id,
        "username": username,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "link": link,
    }


def _reaction_str(reaction_type) -> str:
    """Строковое представление реакции."""
    if reaction_type is None:
        return "—"
    emoji = getattr(reaction_type, "emoji", None)
    if emoji:
        return emoji
    custom_id = getattr(reaction_type, "custom_emoji_id", None)
    if custom_id:
        return f"custom:{custom_id}"
    return "—"


def make_subscribe_handler(
    get_admins: Callable[[], Set[int]],
    base_dir: str,
):
    """Создать обработчик подписки (new_chat_members)."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.new_chat_members:
            return
        chat = update.effective_chat
        if not chat or chat.type not in ("group", "supergroup"):
            return
        # Игнорируем добавление самого бота
        for user in update.message.new_chat_members:
            if user.is_bot:
                continue
            ts = update.message.date
            ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""
            chat_d = _chat_dict(chat)
            user_d = _user_dict(user)
            await storage.save_event("subscribe", chat_d, user_d, {"timestamp": ts_str})
            text = notifications.format_subscribe_notification(chat_d, user_d, ts_str)
            await notifications.notify_admins(context.bot, get_admins, text)

    return handler


def make_unsubscribe_handler(
    get_admins: Callable[[], Set[int]],
    base_dir: str,
):
    """Создать обработчик отписки (left_chat_member)."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.left_chat_member:
            return
        chat = update.effective_chat
        if not chat or chat.type not in ("group", "supergroup"):
            return
        user = update.message.left_chat_member
        if user.is_bot:
            return
        ts = update.message.date
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""
        chat_d = _chat_dict(chat)
        user_d = _user_dict(user)
        await storage.save_event("unsubscribe", chat_d, user_d, {"timestamp": ts_str})
        text = notifications.format_unsubscribe_notification(chat_d, user_d, ts_str)
        await notifications.notify_admins(context.bot, get_admins, text)

    return handler


def make_reaction_handler(
    get_admins: Callable[[], Set[int]],
    base_dir: str,
):
    """Создать обработчик реакций (MessageReactionUpdated)."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reaction_update = update.message_reaction
        if not reaction_update:
            return
        # user может быть None при анонимных реакциях
        user = reaction_update.user
        if not user:
            return
        chat = reaction_update.chat
        if not chat:
            return
        # Берём новую реакцию (что поставили)
        new_reactions = reaction_update.new_reaction
        reaction_str = "—"
        if new_reactions:
            reaction_str = " ".join(_reaction_str(r) for r in new_reactions)
        ts = reaction_update.date
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""
        chat_d = _chat_dict(chat)
        user_d = _user_dict(user)
        meta = {
            "message_id": reaction_update.message_id,
            "reaction": reaction_str,
            "post_link": notifications.get_post_link(chat_d, reaction_update.message_id),
            "timestamp": ts_str,
        }
        await storage.save_event("reaction", chat_d, user_d, meta)
        text = notifications.format_reaction_notification(
            chat_d,
            user_d,
            reaction_update.message_id,
            reaction_str,
            ts_str,
        )
        await notifications.notify_admins(context.bot, get_admins, text)

    return handler
