"""Append-only журнал согласий (ПДн и маркетинг). Записи не перезаписываются."""

from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from integrations.csv_export import SafeCsvWriter
from integrations.json_storage import load_json, run_storage_io, save_json

_BASE = Path(__file__).resolve().parent.parent
CONSENT_LOG_FILE = Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE) / "consent_log.json"

# Срок хранения: вся история пользователя хранится, пока он взаимодействует с ботом,
# и 3 года после его последней записи (в том числе отзыва согласия). Совпадает с
# разделом 7 Политики на maranius.ru (редакция 1.2).
RETENTION_DAYS = 3 * 365
# Предохранитель размера файла, а не срок хранения: журнал целиком перезаписывается
# при каждой записи. При приближении к лимиту нужен переход на БД.
MAX_EVENTS = 200_000
_LOCK = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_created_at(entry: Dict[str, Any]) -> Optional[datetime]:
    try:
        created_at = datetime.fromisoformat(str(entry.get("created_at", "")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at


def _purge(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    dated: List[tuple] = []
    last_seen: Dict[Any, datetime] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        created_at = _parse_created_at(entry)
        if created_at is None:
            continue
        dated.append((entry, created_at))
        user = entry.get("user_id")
        if user not in last_seen or created_at > last_seen[user]:
            last_seen[user] = created_at
    # Запись хранится, пока у её пользователя есть хотя бы одна запись моложе срока.
    kept = [entry for entry, _ in dated if last_seen[entry.get("user_id")] >= cutoff]
    return kept[-MAX_EVENTS:]


async def append(
    path: Optional[Path] = None,
    *,
    user_id: int,
    event: str,
    value: Optional[bool] = None,
    policy_version: Optional[str] = None,
    purpose: str = "",
    document: str = "",
    document_url: str = "",
    action: str = "",
    source: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Добавить неизменяемую запись о согласии или отзыве."""
    async with _LOCK:
        return await run_storage_io(
            _append_sync,
            path,
            user_id=user_id,
            event=event,
            value=value,
            policy_version=policy_version,
            purpose=purpose,
            document=document,
            document_url=document_url,
            action=action,
            source=source,
            meta=meta,
        )


def _append_sync(
    path: Optional[Path] = None,
    *,
    user_id: int,
    event: str,
    value: Optional[bool] = None,
    policy_version: Optional[str] = None,
    purpose: str = "",
    document: str = "",
    document_url: str = "",
    action: str = "",
    source: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Добавить неизменяемую запись о согласии или отзыве."""
    log_path = Path(path) if path else CONSENT_LOG_FILE
    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "user_id": int(user_id),
        "event": str(event)[:64],
        "source": " ".join(str(source).split())[:64],
        "purpose": " ".join(str(purpose).split())[:64],
        "document": " ".join(str(document).split())[:64],
        "document_url": str(document_url).strip()[:500],
        "action": " ".join(str(action).split())[:128],
        "meta": meta or {},
    }
    if value is not None:
        entry["value"] = bool(value)
    if policy_version:
        entry["policy_version"] = str(policy_version)[:32]
    store = load_json(log_path, {"events": []}, allow_recovery=False)
    if not isinstance(store, dict):
        store = {"events": []}
    events = store.get("events", [])
    if not isinstance(events, list):
        events = []
    events.append(entry)
    store["events"] = _purge(events)
    save_json(log_path, store, trailing_newline=True)
    return entry


async def recent(path: Optional[Path] = None, *, user_id: Optional[int] = None, limit: int = 20) -> List[Dict[str, Any]]:
    async with _LOCK:
        return await run_storage_io(_recent_sync, path, user_id=user_id, limit=limit)


def _recent_sync(path: Optional[Path] = None, *, user_id: Optional[int] = None, limit: int = 20) -> List[Dict[str, Any]]:
    log_path = Path(path) if path else CONSENT_LOG_FILE
    store = load_json(log_path, {"events": []}, allow_recovery=False)
    events = store.get("events", []) if isinstance(store, dict) else []
    if not isinstance(events, list):
        return []
    rows = [e for e in events if isinstance(e, dict)]
    if user_id is not None:
        rows = [e for e in rows if e.get("user_id") == int(user_id)]
    return list(reversed(rows[-limit:]))


async def export_csv_bytes(path: Optional[Path] = None) -> bytes:
    """Выгрузить журнал согласий для локального контролируемого хранения."""
    async with _LOCK:
        return await run_storage_io(_export_csv_bytes_sync, path)


def _export_csv_bytes_sync(path: Optional[Path] = None) -> bytes:
    """Выгрузить журнал согласий для локального контролируемого хранения."""
    log_path = Path(path) if path else CONSENT_LOG_FILE
    store = load_json(log_path, {"events": []}, allow_recovery=False)
    rows = store.get("events", []) if isinstance(store, dict) else []
    rows = [entry for entry in rows if isinstance(entry, dict)]

    buf = io.StringIO()
    writer = SafeCsvWriter(buf)
    writer.writerow(
        [
            "id",
            "created_at",
            "user_id",
            "event",
            "value",
            "purpose",
            "document",
            "policy_version",
            "document_url",
            "action",
            "source",
            "meta_json",
        ]
    )
    for row in rows:
        value = row.get("value")
        writer.writerow(
            [
                row.get("id", ""),
                row.get("created_at", ""),
                row.get("user_id", ""),
                row.get("event", ""),
                "" if value is None else ("1" if value else "0"),
                row.get("purpose", ""),
                row.get("document", ""),
                row.get("policy_version", ""),
                row.get("document_url", ""),
                row.get("action", ""),
                row.get("source", ""),
                json.dumps(row.get("meta", {}), ensure_ascii=False),
            ]
        )
    return buf.getvalue().encode("utf-8-sig")
