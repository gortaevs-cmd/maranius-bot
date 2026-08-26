"""Расширенный реестр пользователей Telegram (users.json)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pytz

from integrations.json_storage import load_json, save_json

_BASE = Path(__file__).resolve().parent.parent
USERS_FILE = Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE) / "users.json"

# Глобальный lock для чтения/записи users.json.
# Все операции load -> modify -> save должны выполняться под этим lock.
users_lock = asyncio.Lock()

POLICY_VERSION = "2024-08-03"
MSK = pytz.timezone("Europe/Moscow")

# Поля, которые merge при ensure_user_saved не затирает.
_PRESERVE_KEYS = (
    "first_seen",
    "last_location",
    "timezone",
    "last_weather_text",
    "daily_practice",
    "vip",
    "vip_granted_at",
    "vip_source",
    "policy_accepted_at",
    "policy_version",
    "marketing_opt_in",
    "marketing_opt_in_at",
    "marketing_offer_shown_at",
    "bot_status",
    "blocked_at",
    "unsubscribed_at",
    "resubscribed_at",
    "admin_blocked",
    "admin_blocked_at",
    "is_internal",
    "marketing_opt_out_at",
    "start_param",
    "start_param_at",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_users() -> Dict[str, Any]:
    data = load_json(USERS_FILE, {})
    if not isinstance(data, dict):
        raise ValueError(f"users.json должен содержать JSON-объект: {USERS_FILE}")
    return data


def save_users(users: Dict[str, Any]) -> None:
    save_json(USERS_FILE, users, trailing_newline=True)


def get_user(users: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    row = users.get(str(user_id))
    return row if isinstance(row, dict) else {}


def merge_telegram_profile(
    users: Dict[str, Any],
    *,
    user_id: int,
    username: Optional[str],
    first_name: str,
    last_name: str,
    language_code: str,
    is_premium: bool,
    seed_admin_ids: Set[int],
    force_vip: bool = False,
) -> Tuple[Dict[str, Any], bool]:
    """
    Обновить профиль из Telegram update.
    Returns (record, is_new_user).
    """
    uid = str(user_id)
    now = now_iso()
    is_new = uid not in users
    prev = users.get(uid, {}) if isinstance(users.get(uid), dict) else {}

    record: Dict[str, Any] = {
        "id": user_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name or "",
        "language_code": language_code or "",
        "is_premium": is_premium,
        "last_seen": now,
        "first_seen": prev.get("first_seen") or now,
    }

    for key in _PRESERVE_KEYS:
        if key in prev and key not in record:
            record[key] = prev[key]

    if user_id in seed_admin_ids:
        record["is_internal"] = True
        record["vip"] = True
        if not record.get("vip_granted_at"):
            record["vip_granted_at"] = now
        if not record.get("vip_source"):
            record["vip_source"] = "seed_admin"

    if force_vip and not record.get("vip"):
        record["vip"] = True
        record["vip_granted_at"] = record.get("vip_granted_at") or now

    if record.get("bot_status") is None:
        record["bot_status"] = "active"

    users[uid] = record
    return record, is_new


def has_policy(users: Dict[str, Any], user_id: int) -> bool:
    row = get_user(users, user_id)
    return bool(row.get("policy_accepted_at"))


def has_current_policy(users: Dict[str, Any], user_id: int) -> bool:
    """Актуальная версия политики принята (для gate и доступа к функциям)."""
    row = get_user(users, user_id)
    if not row.get("policy_accepted_at"):
        return False
    return row.get("policy_version") == POLICY_VERSION


def accept_policy(users: Dict[str, Any], user_id: int) -> None:
    uid = str(user_id)
    row = users.setdefault(uid, {"id": user_id})
    row["policy_accepted_at"] = now_iso()
    row["policy_version"] = POLICY_VERSION


def set_marketing_opt_in(users: Dict[str, Any], user_id: int, value: bool) -> None:
    uid = str(user_id)
    row = users.setdefault(uid, {"id": user_id})
    row["marketing_opt_in"] = value
    ts = now_iso()
    if value:
        row["marketing_opt_in_at"] = ts
        row.pop("marketing_opt_out_at", None)
    else:
        row["marketing_opt_out_at"] = ts


def capture_start_param(users: Dict[str, Any], user_id: int, payload: str) -> bool:
    """Сохранить deep-link /start payload один раз. Returns True если записали впервые."""
    text = " ".join((payload or "").split())[:200]
    if not text:
        return False
    uid = str(user_id)
    row = users.setdefault(uid, {"id": user_id})
    if row.get("start_param"):
        return False
    row["start_param"] = text
    row["start_param_at"] = now_iso()
    return True


def mark_marketing_offer_shown(users: Dict[str, Any], user_id: int) -> None:
    uid = str(user_id)
    row = users.setdefault(uid, {"id": user_id})
    row["marketing_offer_shown_at"] = now_iso()


def marketing_offer_was_shown(users: Dict[str, Any], user_id: int) -> bool:
    return bool(get_user(users, user_id).get("marketing_offer_shown_at"))


def is_admin_blocked(users: Dict[str, Any], user_id: int) -> bool:
    return bool(get_user(users, user_id).get("admin_blocked"))


def set_admin_blocked(users: Dict[str, Any], user_id: int, blocked: bool) -> None:
    uid = str(user_id)
    row = users.setdefault(uid, {"id": user_id})
    row["admin_blocked"] = blocked
    row["admin_blocked_at"] = now_iso() if blocked else None


def set_bot_status(users: Dict[str, Any], user_id: int, status: str) -> None:
    uid = str(user_id)
    row = users.setdefault(uid, {"id": user_id})
    row["bot_status"] = status
    ts = now_iso()
    if status == "blocked":
        row["blocked_at"] = ts
        row["unsubscribed_at"] = ts
    elif status == "active":
        row["resubscribed_at"] = ts
        row.pop("unsubscribed_at", None)


def grant_vip(
    users: Dict[str, Any],
    user_id: int,
    *,
    source: str = "admin_grant",
) -> bool:
    """Returns True если VIP выдан впервые."""
    uid = str(user_id)
    row = users.setdefault(uid, {"id": user_id})
    if row.get("vip"):
        return False
    row["vip"] = True
    row["vip_granted_at"] = now_iso()
    row["vip_source"] = source
    return True


def revoke_vip(users: Dict[str, Any], user_id: int) -> bool:
    uid = str(user_id)
    row = users.get(uid)
    if not isinstance(row, dict) or not row.get("vip"):
        return False
    row["vip"] = False
    row["vip_revoked_at"] = now_iso()
    return True


def is_vip(users: Dict[str, Any], user_id: int, *, seed_admin_ids: Set[int]) -> bool:
    if user_id in seed_admin_ids:
        return True
    return bool(get_user(users, user_id).get("vip"))


def parse_user_ref(raw: str) -> Optional[int]:
    """@username не резолвим — только numeric id."""
    s = (raw or "").strip().lstrip("@")
    if s.isdigit():
        return int(s)
    return None


def find_user_id_by_username(users: Dict[str, Any], username: str) -> Optional[int]:
    un = (username or "").strip().lstrip("@").casefold()
    if not un:
        return None
    for uid, row in users.items():
        if not isinstance(row, dict):
            continue
        u = (row.get("username") or "").casefold()
        if u == un:
            try:
                return int(uid)
            except ValueError:
                continue
    return None


def _parse_iso(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def days_since_last_seen(row: Dict[str, Any]) -> Optional[int]:
    dt = _parse_iso(str(row.get("last_seen") or ""))
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).days


def segment_filter(name: str) -> Callable[[Dict[str, Any]], bool]:
    """Предикат сегмента для экспорта."""
    if name in ("subscribed", "available"):
        return lambda r: r.get("bot_status", "active") != "blocked"
    if name in ("unsubscribed", "bot_blocked"):
        return lambda r: r.get("bot_status") == "blocked"
    if name == "no_policy":
        return lambda r: not r.get("policy_accepted_at") or r.get("policy_version") != POLICY_VERSION
    if name == "with_policy":
        return lambda r: bool(r.get("policy_accepted_at")) and r.get("policy_version") == POLICY_VERSION
    if name in ("marketing", "marketing_opt_in"):
        return lambda r: bool(r.get("marketing_opt_in"))
    if name in ("vip", "vip_access"):
        return lambda r: bool(r.get("vip"))
    if name == "marketing_ready":
        return lambda r: (
            bool(r.get("marketing_opt_in"))
            and bool(r.get("policy_accepted_at"))
            and r.get("policy_version") == POLICY_VERSION
            and r.get("bot_status", "active") != "blocked"
            and not bool(r.get("admin_blocked"))
            and not bool(r.get("is_internal"))
        )
    if name == "admin_blocked":
        return lambda r: bool(r.get("admin_blocked"))
    if name == "active_7":
        return lambda r: (d := days_since_last_seen(r)) is not None and d <= 7
    if name == "active_30":
        return lambda r: (d := days_since_last_seen(r)) is not None and d <= 30
    if name == "sleeping":
        return lambda r: (d := days_since_last_seen(r)) is not None and d > 30
    if name == "internal":
        return lambda r: bool(r.get("is_internal"))
    if name == "real":
        return lambda r: not r.get("is_internal")
    return lambda _r: True


def export_users_csv(
    users: Dict[str, Any],
    *,
    segment: Optional[str] = None,
) -> bytes:
    import csv
    import io

    pred = segment_filter(segment) if segment else lambda _r: True
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "id",
            "username",
            "first_name",
            "last_name",
            "language_code",
            "timezone",
            "bot_status",
            "blocked_at",
            "policy_accepted_at",
            "policy_version",
            "marketing_opt_in",
            "marketing_opt_in_at",
            "marketing_opt_out_at",
            "start_param",
            "vip",
            "vip_source",
            "vip_granted_at",
            "admin_blocked",
            "is_internal",
            "first_seen",
            "last_seen",
        ]
    )
    for _uid, row in sorted(users.items(), key=lambda x: x[0]):
        if not isinstance(row, dict) or not pred(row):
            continue
        writer.writerow(
            [
                row.get("id", ""),
                row.get("username") or "",
                row.get("first_name") or "",
                row.get("last_name") or "",
                row.get("language_code") or "",
                row.get("timezone") or "",
                row.get("bot_status") or "active",
                row.get("blocked_at") or "",
                row.get("policy_accepted_at") or "",
                row.get("policy_version") or "",
                "1" if row.get("marketing_opt_in") else "0",
                row.get("marketing_opt_in_at") or "",
                row.get("marketing_opt_out_at") or "",
                row.get("start_param") or "",
                "1" if row.get("vip") else "0",
                row.get("vip_source") or "",
                row.get("vip_granted_at") or "",
                "1" if row.get("admin_blocked") else "0",
                "1" if row.get("is_internal") else "0",
                row.get("first_seen") or "",
                row.get("last_seen") or "",
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def stats_summary(users: Dict[str, Any]) -> Dict[str, int]:
    """Сводка для отчётов и /god."""
    out = {
        "total": 0,
        "real": 0,
        "new_7d": 0,
        "active_7": 0,
        "active_30": 0,
        "sleeping": 0,
        "vip": 0,
        "no_policy": 0,
        "marketing": 0,
        "blocked": 0,
        "admin_blocked": 0,
    }
    now = datetime.now(timezone.utc)
    for row in users.values():
        if not isinstance(row, dict):
            continue
        out["total"] += 1
        if row.get("is_internal"):
            continue
        out["real"] += 1
        if row.get("vip"):
            out["vip"] += 1
        if not row.get("policy_accepted_at") or row.get("policy_version") != POLICY_VERSION:
            out["no_policy"] += 1
        if row.get("marketing_opt_in"):
            out["marketing"] += 1
        if row.get("bot_status") == "blocked":
            out["blocked"] += 1
        if row.get("admin_blocked"):
            out["admin_blocked"] += 1
        fs = _parse_iso(str(row.get("first_seen") or ""))
        if fs and (now - fs).days <= 7:
            out["new_7d"] += 1
        d = days_since_last_seen(row)
        if d is not None:
            if d <= 7:
                out["active_7"] += 1
            if d <= 30:
                out["active_30"] += 1
            if d > 30:
                out["sleeping"] += 1
    return out


VIP_SOURCE_LABELS: Dict[str, str] = {
    "code": "активирован VIP-код",
    "admin_grant": "выдан администратором",
    "import": "импортирован из списка",
    "seed_admin": "служебный доступ",
}


def vip_source_label(source: Optional[str]) -> str:
    if not source:
        return "не указан"
    return VIP_SOURCE_LABELS.get(str(source), str(source))


def show_marketing_subscribe_in_main_menu(
    users: Dict[str, Any],
    user_id: int,
    *,
    seed_admin_ids: Set[int],
) -> bool:
    """Показывать кнопку «Подписаться на рассылку» в нижнем меню."""
    if user_id in seed_admin_ids:
        return False
    row = get_user(users, user_id)
    if row.get("is_internal"):
        return False
    if not has_current_policy(users, user_id):
        return False
    return not bool(row.get("marketing_opt_in"))


def main_reply_keyboard(
    users: Dict[str, Any],
    user_id: int,
    *,
    seed_admin_ids: Set[int],
):
    """Reply-клавиатура с учётом статуса рассылки пользователя."""
    import ui as ui_mod

    show = show_marketing_subscribe_in_main_menu(users, user_id, seed_admin_ids=seed_admin_ids)
    return ui_mod.get_main_keyboard(show_marketing_subscribe=show)
