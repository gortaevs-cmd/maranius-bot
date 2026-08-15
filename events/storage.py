"""Хранение событий: подписки, отписки, реакции."""
import asyncio
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from integrations.json_storage import load_json, save_json

_events_lock = asyncio.Lock()
_events_file: Optional[str] = None


def init_storage(base_dir: str) -> None:
    """Инициализировать путь к файлу событий."""
    global _events_file
    _events_file = os.path.join(base_dir, "events.json")


def _load_events() -> Dict[str, Any]:
    """Загрузить события из файла."""
    if not _events_file:
        return {"events": [], "schema_version": 1}
    return load_json(_events_file, {"events": [], "schema_version": 1})


def _save_events(data: Dict[str, Any]) -> None:
    """Сохранить события в файл."""
    if not _events_file:
        return
    save_json(_events_file, data, trailing_newline=True)


async def save_event(
    event_type: str,
    chat: Dict[str, Any],
    user: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Сохранить событие и вернуть его id.
    event_type: subscribe | unsubscribe | reaction
    """
    event_id = str(uuid.uuid4())
    record = {
        "id": event_id,
        "type": event_type,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chat": chat,
        "user": user,
        "meta": meta or {},
    }
    async with _events_lock:
        data = _load_events()
        data.setdefault("events", []).append(record)
        # Ограничиваем историю (последние 10000 событий)
        if len(data["events"]) > 10000:
            data["events"] = data["events"][-10000:]
        _save_events(data)
    return event_id


def get_monitored_chats(base_dir: str) -> Set[int]:
    """Загрузить список отслеживаемых чатов."""
    path = os.path.join(base_dir, "monitored_chats.json")
    data = load_json(path, {"chat_ids": []})
    return set(data.get("chat_ids", []))


def add_monitored_chat(base_dir: str, chat_id: int) -> None:
    """Добавить чат в список отслеживаемых."""
    path = os.path.join(base_dir, "monitored_chats.json")
    chats = get_monitored_chats(base_dir)
    chats.add(chat_id)
    save_json(path, {"chat_ids": list(chats)}, trailing_newline=True)


def get_events_stats() -> Dict[str, Any]:
    """Получить статистику событий для админ-панели."""
    data = _load_events()
    events = data.get("events", [])

    subscribe_count = sum(1 for e in events if e.get("type") == "subscribe")
    unsubscribe_count = sum(1 for e in events if e.get("type") == "unsubscribe")
    reaction_count = sum(1 for e in events if e.get("type") == "reaction")

    # Последние 10 событий (новые сверху)
    last_events = list(reversed(events[-10:])) if events else []

    return {
        "total": len(events),
        "subscribe": subscribe_count,
        "unsubscribe": unsubscribe_count,
        "reaction": reaction_count,
        "last_events": last_events,
    }
