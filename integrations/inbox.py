"""Inbox: необработанные сообщения и неизвестные ангелы."""

from __future__ import annotations

import asyncio
import csv
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from integrations.json_storage import load_json, save_json

_LOCK = asyncio.Lock()
_BASE = Path(__file__).resolve().parent.parent
INBOX_FILE = _BASE / "data" / "inbox.json"
LEGACY_UNKNOWN_CSV = _BASE / "data" / "angelic" / "unknown_angelic.csv"
RETENTION_DAYS = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_store() -> Dict[str, Any]:
    return {"entries": []}


def _load_unlocked() -> Dict[str, Any]:
    data = load_json(INBOX_FILE, _empty_store())
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("entries", [])
    return data


def _save_unlocked(data: Dict[str, Any]) -> None:
    save_json(INBOX_FILE, data, trailing_newline=True)


async def load_store() -> Dict[str, Any]:
    async with _LOCK:
        return _load_unlocked()


def _purge_old_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    kept: List[Dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        raw = row.get("created_at") or ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            kept.append(row)
            continue
        if dt >= cutoff:
            kept.append(row)
    return kept


async def add_entry(
    *,
    entry_type: str,
    user_id: int,
    username: Optional[str],
    text: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Добавить запись inbox. Returns entry dict."""
    entry = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "type": entry_type,
        "user_id": user_id,
        "username": username or "",
        "text": (text or "")[:2000],
        "meta": meta or {},
        "exported_at": None,
        "admin_notified_at": None,
    }
    async with _LOCK:
        data = _load_unlocked()
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        entries.append(entry)
        data["entries"] = _purge_old_entries(entries)
        _save_unlocked(data)
    return entry


async def mark_notified(entry_id: str) -> None:
    async with _LOCK:
        data = _load_unlocked()
        for row in data.get("entries", []):
            if isinstance(row, dict) and row.get("id") == entry_id:
                row["admin_notified_at"] = _now_iso()
                break
        _save_unlocked(data)


def _entry_notified(row: Dict[str, Any]) -> bool:
    return bool(row.get("admin_notified_at"))


async def stats() -> Tuple[int, int]:
    """total, unnotified."""
    data = await load_store()
    entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
    unnotified = sum(1 for e in entries if not _entry_notified(e))
    return len(entries), unnotified


async def export_csv_bytes() -> Tuple[bytes, str]:
    """
    CSV выгрузка: каждой строке downloaded_at = время выгрузки.
    Returns (bytes, download_ts).
    """
    download_ts = _now_iso()
    async with _LOCK:
        data = _load_unlocked()
        entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
        for row in entries:
            row["exported_at"] = download_ts
        data["entries"] = _purge_old_entries(entries)
        _save_unlocked(data)

    import io

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "id",
            "created_at",
            "type",
            "user_id",
            "username",
            "text",
            "meta_json",
            "downloaded_at",
            "exported_at",
        ]
    )
    for row in entries:
        meta = row.get("meta") or {}
        writer.writerow(
            [
                row.get("id", ""),
                row.get("created_at", ""),
                row.get("type", ""),
                row.get("user_id", ""),
                row.get("username", ""),
                row.get("text", ""),
                json.dumps(meta, ensure_ascii=False) if meta else "",
                download_ts,
                row.get("exported_at") or download_ts,
            ]
        )
    return buf.getvalue().encode("utf-8-sig"), download_ts


async def migrate_legacy_unknown_csv() -> int:
    """Импорт unknown_angelic.csv → inbox (один раз). Returns imported count."""
    if not LEGACY_UNKNOWN_CSV.is_file() or LEGACY_UNKNOWN_CSV.stat().st_size == 0:
        return 0
    imported = 0
    rows: List[Dict[str, str]] = []
    with LEGACY_UNKNOWN_CSV.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for row in reader:
            rows.append(row)

    async with _LOCK:
        data = _load_unlocked()
        existing_ids = {
            (e.get("meta") or {}).get("legacy_csv_row")
            for e in data.get("entries", [])
            if isinstance(e, dict)
        }
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        for i, row in enumerate(rows):
            legacy_key = f"csv:{i}:{row.get('datetime_utc', '')}:{row.get('normalized', '')}"
            if legacy_key in existing_ids:
                continue
            try:
                uid = int(row.get("user_id") or 0)
            except (ValueError, TypeError):
                uid = 0
            entry = {
                "id": uuid.uuid4().hex[:12],
                "created_at": row.get("datetime_utc") or _now_iso(),
                "type": "unknown_angel",
                "user_id": uid,
                "username": row.get("username") or "",
                "text": row.get("raw_input") or row.get("normalized") or "",
                "meta": {
                    "normalized": row.get("normalized", ""),
                    "legacy_csv_row": legacy_key,
                    "migrated_from": "unknown_angelic.csv",
                    "migrated": True,
                },
                "exported_at": None,
                "admin_notified_at": _now_iso(),
            }
            entries.append(entry)
            imported += 1
        data["entries"] = _purge_old_entries(entries)
        _save_unlocked(data)
    return imported
