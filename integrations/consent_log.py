"""Append-only журнал согласий (ПДн и маркетинг). Записи не перезаписываются."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from integrations.json_storage import load_json, save_json

_BASE = Path(__file__).resolve().parent.parent
CONSENT_LOG_FILE = Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE) / "consent_log.json"

RETENTION_DAYS = 365
MAX_EVENTS = 50_000
_LOCK = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _purge(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    kept: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            created_at = datetime.fromisoformat(str(entry.get("created_at", "")).replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if created_at >= cutoff:
            kept.append(entry)
    return kept[-MAX_EVENTS:]


async def append(
    path: Optional[Path] = None,
    *,
    user_id: int,
    event: str,
    value: Optional[bool] = None,
    policy_version: Optional[str] = None,
    source: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Добавить запись о согласии или отзыве."""
    log_path = Path(path) if path else CONSENT_LOG_FILE
    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "user_id": int(user_id),
        "event": str(event)[:64],
        "source": " ".join(str(source).split())[:64],
        "meta": meta or {},
    }
    if value is not None:
        entry["value"] = bool(value)
    if policy_version:
        entry["policy_version"] = str(policy_version)[:32]
    async with _LOCK:
        store = load_json(log_path, {"events": []})
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
    log_path = Path(path) if path else CONSENT_LOG_FILE
    async with _LOCK:
        store = load_json(log_path, {"events": []})
    events = store.get("events", []) if isinstance(store, dict) else []
    if not isinstance(events, list):
        return []
    rows = [e for e in events if isinstance(e, dict)]
    if user_id is not None:
        rows = [e for e in rows if e.get("user_id") == int(user_id)]
    return list(reversed(rows[-limit:]))


async def export_csv_bytes(path: Optional[Path] = None) -> bytes:
    """Выгрузить журнал согласий для локального контролируемого хранения."""
    log_path = Path(path) if path else CONSENT_LOG_FILE
    async with _LOCK:
        store = load_json(log_path, {"events": []})
        rows = store.get("events", []) if isinstance(store, dict) else []
        rows = [entry for entry in rows if isinstance(entry, dict)]

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        ["id", "created_at", "user_id", "event", "value", "policy_version", "source", "meta_json"]
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
                row.get("policy_version", ""),
                row.get("source", ""),
                json.dumps(row.get("meta", {}), ensure_ascii=False),
            ]
        )
    return buf.getvalue().encode("utf-8-sig")
