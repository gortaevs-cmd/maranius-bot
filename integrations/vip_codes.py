"""VIP-коды: хранение, активация, выгрузка, импорт пользователей."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from integrations.json_storage import load_json, save_json

_lock = asyncio.Lock()
_BASE = Path(__file__).resolve().parent.parent
CODES_FILE = _BASE / "data" / "vip" / "codes.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_code(raw: str) -> str:
    return (raw or "").strip().casefold()


def _empty_store() -> Dict[str, Any]:
    return {"active": [], "used": []}


def _load_unlocked() -> Dict[str, Any]:
    data = load_json(CODES_FILE, _empty_store())
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("active", [])
    data.setdefault("used", [])
    return data


def _save_unlocked(data: Dict[str, Any]) -> None:
    save_json(CODES_FILE, data, trailing_newline=True)


async def load_store() -> Dict[str, Any]:
    async with _lock:
        return _load_unlocked()


async def save_store(data: Dict[str, Any]) -> None:
    async with _lock:
        _save_unlocked(data)


def _active_set(data: Dict[str, Any]) -> Set[str]:
    return {normalize_code(c) for c in data.get("active", []) if c}


def _used_codes(data: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for row in data.get("used", []):
        if isinstance(row, dict):
            code = normalize_code(str(row.get("code", "")))
            if code:
                out.add(code)
    return out


def _analyze_codes_bulk(data: Dict[str, Any], raw_text: str) -> Tuple[List[str], int, int]:
    """Вернуть новые коды, дубли и пустые строки без изменения хранилища."""
    active = _active_set(data)
    used = _used_codes(data)
    new_codes: List[str] = []
    duplicates = skipped = 0
    for line in (raw_text or "").splitlines():
        raw = line.strip()
        if not raw:
            skipped += 1
            continue
        if raw.startswith("#"):
            continue
        norm = normalize_code(raw)
        if not norm:
            skipped += 1
            continue
        if norm in active or norm in used:
            duplicates += 1
            continue
        new_codes.append(norm)
        active.add(norm)
    return new_codes, duplicates, skipped


async def preview_codes_bulk(raw_text: str) -> Tuple[int, int, int]:
    """Предпросмотр добавления кодов: new, duplicates, blank_lines."""
    async with _lock:
        data = _load_unlocked()
        new_codes, duplicates, skipped = _analyze_codes_bulk(data, raw_text)
    return len(new_codes), duplicates, skipped


async def add_codes_bulk(raw_text: str) -> Tuple[int, int, int]:
    """Добавить коды списком. Returns (added, duplicates, skipped_empty)."""
    async with _lock:
        data = _load_unlocked()
        new_codes, duplicates, skipped = _analyze_codes_bulk(data, raw_text)
        new_active: List[str] = list(data.get("active", []))
        new_active.extend(new_codes)
        data["active"] = new_active
        _save_unlocked(data)
        return len(new_codes), duplicates, skipped


async def redeem_code(
    raw: str,
    *,
    user_id: int,
    username: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Активировать код. Returns (ok, reason).
    reason: ok | invalid | already_used
    """
    norm = normalize_code(raw)
    if not norm:
        return False, "invalid"
    async with _lock:
        data = _load_unlocked()
        active_list = data.get("active", [])
        active_norm = [normalize_code(c) for c in active_list]
        if norm not in active_norm:
            if norm in _used_codes(data):
                return False, "already_used"
            return False, "invalid"
        idx = active_norm.index(norm)
        active_list.pop(idx)
        data["active"] = active_list
        used = data.get("used", [])
        used.append(
            {
                "code": norm,
                "user_id": user_id,
                "username": username,
                "used_at": _now_iso(),
            }
        )
        data["used"] = used
        _save_unlocked(data)
        return True, "ok"


async def rollback_redemption(raw: str, *, user_id: int) -> bool:
    """Вернуть код в активные, если выдача VIP не завершилась."""
    norm = normalize_code(raw)
    async with _lock:
        data = _load_unlocked()
        used = data.get("used", [])
        for index in range(len(used) - 1, -1, -1):
            row = used[index]
            if isinstance(row, dict) and normalize_code(str(row.get("code", ""))) == norm and row.get("user_id") == user_id:
                used.pop(index)
                active = data.get("active", [])
                if norm not in _active_set(data):
                    active.append(norm)
                data["active"] = active
                data["used"] = used
                _save_unlocked(data)
                return True
    return False


async def mark_code_used(
    raw: str,
    *,
    user_id: int,
    username: Optional[str] = None,
    source: str = "admin_approved_invalid_code",
) -> bool:
    """Пометить код из ручного VIP-одобрения как отработанный.

    Операция идемпотентна: повторное нажатие по тому же алерту не создаёт дубль.
    """
    norm = normalize_code(raw)
    if not norm:
        return False
    async with _lock:
        data = _load_unlocked()
        if norm in _used_codes(data):
            return False
        data["active"] = [
            code for code in data.get("active", []) if normalize_code(str(code)) != norm
        ]
        used = data.get("used", [])
        used.append(
            {
                "code": norm,
                "user_id": user_id,
                "username": username,
                "used_at": _now_iso(),
                "source": source,
            }
        )
        data["used"] = used
        _save_unlocked(data)
        return True


async def counts() -> Tuple[int, int]:
    data = await load_store()
    return len(data.get("active", [])), len(data.get("used", []))


async def export_csv_bytes() -> bytes:
    """Один CSV: code, status, user_id, username, used_at."""
    data = await load_store()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["code", "status", "user_id", "username", "used_at"])
    for code in data.get("active", []):
        writer.writerow([code, "active", "", "", ""])
    for row in data.get("used", []):
        if not isinstance(row, dict):
            continue
        writer.writerow(
            [
                row.get("code", ""),
                "used",
                row.get("user_id", ""),
                row.get("username", "") or "",
                row.get("used_at", "") or "",
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def parse_vip_user_ids(raw_text: str) -> Tuple[List[int], int]:
    """
    Разбор списка telegram_id (по строке или CSV).
    Returns (ids, invalid_lines).
    """
    ids: List[int] = []
    invalid = 0
    for line in (raw_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cell = line.split(";")[0].split(",")[0].strip()
        try:
            ids.append(int(cell))
        except ValueError:
            invalid += 1
    return ids, invalid


def grant_vip_in_users(
    users: Dict[str, Any],
    user_id: int,
    *,
    source: str = "code",
) -> bool:
    """Пометить пользователя VIP в users.json. Returns True если новый grant."""
    uid = str(user_id)
    row = users.setdefault(uid, {})
    if row.get("vip"):
        return False
    row["vip"] = True
    row["vip_granted_at"] = _now_iso()
    row["vip_source"] = source
    return True


def find_used_code_owner(norm: str) -> Optional[Dict[str, Any]]:
    """Кто первым активировал код (из used[])."""
    code = normalize_code(norm)
    if not code:
        return None
    data = _load_unlocked()
    for row in data.get("used", []):
        if not isinstance(row, dict):
            continue
        if normalize_code(str(row.get("code", ""))) == code:
            return row
    return None


def is_user_vip(users: Dict[str, Any], user_id: int) -> bool:
    row = users.get(str(user_id), {})
    return bool(isinstance(row, dict) and row.get("vip"))
