"""Хранение событий: подписки, отписки, реакции."""
import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

_events_lock = asyncio.Lock()
_events_file: Optional[str] = None


def init_storage(base_dir: str) -> None:
    """Инициализировать путь к файлу событий."""
    global _events_file
    _events_file = os.path.join(base_dir, "events.json")


def _load_events() -> Dict[str, Any]:
    """Загрузить события из файла."""
    if not _events_file or not os.path.exists(_events_file):
        return {"events": [], "schema_version": 1}
    try:
        with open(_events_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"events": [], "schema_version": 1}


def _save_events(data: Dict[str, Any]) -> None:
    """Сохранить события в файл."""
    if not _events_file:
        return
    with open(_events_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("chat_ids", []))
    except (json.JSONDecodeError, OSError):
        return set()


def add_monitored_chat(base_dir: str, chat_id: int) -> None:
    """Добавить чат в список отслеживаемых."""
    path = os.path.join(base_dir, "monitored_chats.json")
    chats = get_monitored_chats(base_dir)
    chats.add(chat_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"chat_ids": list(chats)}, f, ensure_ascii=False, indent=2)


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
