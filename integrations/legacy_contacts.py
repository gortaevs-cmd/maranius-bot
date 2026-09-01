"""One-time legacy contact migration and reactivation registry.

The registry deliberately contains only Telegram IDs of people who were not
present in the verified delivery list.  It is separate from ``users.json`` so
such contacts cannot enter normal segments until they return to the bot and
accept the current mandatory documents.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from integrations.json_storage import load_json, save_json

_BASE = Path(__file__).resolve().parent.parent
INACTIVE_USERS_FILE = (
    Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE) / "legacy_inactive_users.json"
)
PENDING_MIGRATION_FILE = (
    Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE) / "legacy_migration_pending.json"
)
LAST_MIGRATION_FILE = (
    Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE) / "legacy_migration_last_result.json"
)
SCHEMA_VERSION = 1
_lock = asyncio.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_user_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Telegram ID cannot be boolean")
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"Invalid Telegram ID: {value!r}")
    user_id = int(text)
    if user_id <= 0:
        raise ValueError(f"Invalid Telegram ID: {value!r}")
    return user_id


def normalize_ids(
    values: Iterable[Any], *, label: str, allow_empty: bool = False
) -> Tuple[set[int], int]:
    """Validate an ID iterable and return a deduplicated set and duplicate count."""
    normalized: set[int] = set()
    duplicates = 0
    for value in values:
        user_id = normalize_user_id(value)
        if user_id in normalized:
            duplicates += 1
            continue
        normalized.add(user_id)
    if not normalized and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    return normalized, duplicates


def empty_inactive_store() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "records": {}}


def load_inactive_store(path: Path | None = None) -> Dict[str, Any]:
    path = path or INACTIVE_USERS_FILE
    data = load_json(path, empty_inactive_store())
    if not isinstance(data, dict):
        raise ValueError(f"Inactive legacy store must be an object: {path}")
    records = data.get("records")
    if not isinstance(records, dict):
        raise ValueError(f"Inactive legacy store records must be an object: {path}")
    data["schema_version"] = SCHEMA_VERSION
    data["records"] = records
    return data


def save_inactive_store(
    data: Dict[str, Any], path: Path | None = None
) -> None:
    path = path or INACTIVE_USERS_FILE
    save_json(path, data, trailing_newline=True)


def apply_legacy_migration(
    users: Dict[str, Any],
    inactive_store: Dict[str, Any],
    *,
    active_user_ids: Iterable[Any],
    vip_user_ids: Iterable[Any],
    inactive_user_ids: Iterable[Any],
    source: str,
    imported_at: str | None = None,
) -> Dict[str, int]:
    """Merge a validated migration roster without transferring any consent.

    ``active_user_ids`` are users included in the verified delivery list plus
    every historical VIP.  ``inactive_user_ids`` are stored separately until
    the person returns to the bot.  Existing Maranius access is never
    downgraded to inactive and existing operational fields are preserved.
    """
    if not isinstance(users, dict):
        raise ValueError("users must be an object")
    if not isinstance(inactive_store, dict):
        raise ValueError("inactive_store must be an object")

    active_ids, active_duplicates = normalize_ids(active_user_ids, label="active_user_ids")
    vip_ids, vip_duplicates = normalize_ids(
        vip_user_ids, label="vip_user_ids", allow_empty=True
    )
    inactive_ids, inactive_duplicates = normalize_ids(
        inactive_user_ids, label="inactive_user_ids"
    )
    if not vip_ids.issubset(active_ids):
        raise ValueError("Every VIP ID must be included in active_user_ids")
    if active_ids & inactive_ids:
        raise ValueError("Active and inactive legacy IDs must not overlap")

    source = " ".join((source or "").split())[:120]
    if not source:
        raise ValueError("source must not be empty")
    imported_at = imported_at or now_iso()

    records = inactive_store.setdefault("records", {})
    if not isinstance(records, dict):
        raise ValueError("inactive_store records must be an object")
    inactive_store["schema_version"] = SCHEMA_VERSION

    result = {
        "active_created": 0,
        "active_existing": 0,
        "vip_granted": 0,
        "vip_already_present": 0,
        "inactive_stored": 0,
        "inactive_existing_active": 0,
        "inactive_already_listed": 0,
        "active_duplicates": active_duplicates,
        "vip_duplicates": vip_duplicates,
        "inactive_duplicates": inactive_duplicates,
    }

    for user_id in sorted(active_ids):
        uid = str(user_id)
        row = users.get(uid)
        if not isinstance(row, dict):
            row = {"id": user_id}
            users[uid] = row
            result["active_created"] += 1
        else:
            result["active_existing"] += 1
            row.setdefault("id", user_id)

        # The verified delivery list is a historical operational signal, not
        # an opt-in to the current marketing consent version.
        row.setdefault("bot_status", "active")
        row.setdefault("legacy_import_source", source)
        row.setdefault("legacy_imported_at", imported_at)

        if user_id in vip_ids:
            if row.get("vip"):
                result["vip_already_present"] += 1
            else:
                row["vip"] = True
                row["vip_granted_at"] = imported_at
                row["vip_source"] = "import"
                result["vip_granted"] += 1

    for user_id in sorted(inactive_ids):
        uid = str(user_id)
        existing = users.get(uid)
        # A person already active in Maranius must never be put back into the
        # reactivation queue by an older legacy export.
        if isinstance(existing, dict) and existing.get("first_seen"):
            result["inactive_existing_active"] += 1
            records.pop(uid, None)
            continue
        if uid in records:
            result["inactive_already_listed"] += 1
            continue
        records[uid] = {
            "id": user_id,
            "source": source,
            "listed_at": imported_at,
        }
        result["inactive_stored"] += 1

    return result


async def claim_returning_inactive_user(user_id: int) -> Dict[str, Any] | None:
    """Remove and return a legacy-inactive record once its owner is active.

    This operation is intentionally idempotent: only the first accepted
    return receives an admin notification.
    """
    uid = str(normalize_user_id(user_id))
    async with _lock:
        data = load_inactive_store()
        record = data["records"].pop(uid, None)
        if not isinstance(record, dict):
            return None
        save_inactive_store(data)
        return record
