"""Аудит изменяющих действий в скрытой панели /god."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from integrations.json_storage import load_json, save_json

RETENTION_DAYS = 365
MAX_EVENTS = 20_000
_LOCK = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_target_ids(target_ids: Iterable[int]) -> List[int]:
    out: List[int] = []
    seen: set[int] = set()
    for value in target_ids:
        try:
            user_id = int(value)
        except (TypeError, ValueError):
            continue
        if user_id > 0 and user_id not in seen:
            out.append(user_id)
            seen.add(user_id)
    return out


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
    path: Path,
    *,
    actor_id: int,
    action: str,
    target_ids: Iterable[int] = (),
    reason: str,
    meta: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Атомарно добавить запись. Причина ограничена, чтобы не собирать лишние ПДн."""
    entry = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "actor_id": int(actor_id),
        "action": str(action)[:80],
        "target_ids": _valid_target_ids(target_ids),
        "reason": " ".join(str(reason).split())[:200],
        "meta": meta or {},
    }
    async with _LOCK:
        store = load_json(path, {"events": []})
        if not isinstance(store, dict):
            store = {"events": []}
        entries = store.get("events", [])
        if not isinstance(entries, list):
            entries = []
        entries.append(entry)
        store["events"] = _purge(entries)
        save_json(path, store, trailing_newline=True)
    return entry


async def recent(path: Path, *, limit: int = 10) -> List[Dict[str, Any]]:
    """Последние записи в порядке от новых к старым."""
    async with _LOCK:
        store = load_json(path, {"events": []})
        entries = store.get("events", []) if isinstance(store, dict) else []
        rows = [entry for entry in entries if isinstance(entry, dict)]
    return list(reversed(rows[-max(1, limit) :]))


async def export_csv_bytes(path: Path) -> bytes:
    """Выгрузить журнал для локального контролируемого хранения."""
    async with _LOCK:
        store = load_json(path, {"events": []})
        rows = store.get("events", []) if isinstance(store, dict) else []
        rows = [entry for entry in rows if isinstance(entry, dict)]

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["id", "created_at", "actor_id", "action", "target_ids", "reason", "meta_json"])
    for row in rows:
        writer.writerow(
            [
                row.get("id", ""),
                row.get("created_at", ""),
                row.get("actor_id", ""),
                row.get("action", ""),
                ",".join(str(value) for value in row.get("target_ids", [])),
                row.get("reason", ""),
                json.dumps(row.get("meta", {}), ensure_ascii=False),
            ]
        )
    return buf.getvalue().encode("utf-8-sig")
