"""Реферальная программа Maranius.

Хранит отдельный журнал, потому что рефералы и баланс — финансово значимые
данные. Все изменения выполняются под одним lock и записываются в ledger
идемпотентными событиями.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

import pytz

from integrations.json_storage import load_json, run_storage_io, save_json

_BASE = Path(__file__).resolve().parent.parent
REFERRALS_FILE = Path(os.getenv("MARANIUS_RUNTIME_DIR") or _BASE) / "referrals.json"
referrals_lock = asyncio.Lock()

REFERRAL_PAYLOAD_PREFIX = "ref_"
REFERRAL_CODE_LENGTH = 8
ACTIVATION_REWARD_POINTS = 100
MIN_ACTIVE_DAYS = 2
MIN_AGE_HOURS = 24
MAX_NEW_REFERRALS_PER_DAY = 30
MAX_REWARDED_REFERRALS_PER_30_DAYS = 50
MSK = pytz.timezone("Europe/Moscow")
_CODE_ALPHABET = string.ascii_uppercase + string.digits

REWARD_CATALOG = {
    "coupon10": {
        "title": "Промокод −10% на цифровой продукт",
        "cost": 300,
        "fulfillment": "manual_coupon",
    },
    "vip30": {
        "title": "VIP-подарок на 30 дней",
        "cost": 500,
        "fulfillment": "manual_vip",
    },
    "consult": {
        "title": "Персональный разбор",
        "cost": 1000,
        "fulfillment": "manual_consultation",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "referrers": {},
        "codes": {},
        "referrals": {},
        "ledger": [],
        "redemptions": [],
    }


def _load() -> Dict[str, Any]:
    data = load_json(REFERRALS_FILE, _default_state())
    if not isinstance(data, dict):
        raise ValueError(f"referrals.json должен содержать JSON-объект: {REFERRALS_FILE}")
    for key, default in _default_state().items():
        if key not in data or not isinstance(data[key], type(default)):
            data[key] = default
    return data


def _save(data: Dict[str, Any]) -> None:
    save_json(REFERRALS_FILE, data, trailing_newline=True)


def _safe_user_id(raw: Any) -> Optional[int]:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_referral_payload(payload: str) -> Optional[str]:
    """Извлечь код только из Telegram deep-link вида ``ref_XXXXXXXX``."""
    value = (payload or "").strip().split()[0] if (payload or "").strip() else ""
    if not value.casefold().startswith(REFERRAL_PAYLOAD_PREFIX):
        return None
    code = value[len(REFERRAL_PAYLOAD_PREFIX):].upper()
    if len(code) != REFERRAL_CODE_LENGTH or any(ch not in _CODE_ALPHABET for ch in code):
        return None
    return code


def _new_code(data: Dict[str, Any]) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH))
        if code not in data["codes"]:
            return code
    raise RuntimeError("Не удалось создать уникальный реферальный код")


async def get_or_create_code(user_id: int) -> str:
    """Вернуть стабильный код пользователя, создав его при первом открытии."""
    async with referrals_lock:
        return await run_storage_io(_get_or_create_code_sync, user_id)


def _get_or_create_code_sync(user_id: int) -> str:
    """Вернуть стабильный код пользователя, создав его при первом открытии."""
    uid = str(user_id)
    data = _load()
    row = data["referrers"].get(uid)
    if isinstance(row, dict) and row.get("code"):
        code = str(row["code"])
        if data["codes"].get(code) != user_id:
            data["codes"][code] = user_id
            _save(data)
        return code
    code = _new_code(data)
    data["referrers"][uid] = {
        "user_id": user_id,
        "code": code,
        "balance": int((row or {}).get("balance") or 0) if isinstance(row, dict) else 0,
        "created_at": str((row or {}).get("created_at") or _iso(_now())) if isinstance(row, dict) else _iso(_now()),
    }
    data["codes"][code] = user_id
    _save(data)
    return code


def _is_blocked_or_internal(row: Dict[str, Any]) -> bool:
    return bool(
        row.get("is_internal")
        or row.get("admin_blocked")
        or row.get("bot_status") == "blocked"
    )


async def register_referral(
    referred_user_id: int,
    payload: str,
    users: Dict[str, Any],
    *,
    excluded_ids: Iterable[int] = (),
    now: Optional[datetime] = None,
) -> str:
    """Закрепить реферера один раз. Возвращает результат для аудита/UI."""
    async with referrals_lock:
        return await run_storage_io(
            _register_referral_sync,
            referred_user_id,
            payload,
            users,
            excluded_ids=excluded_ids,
            now=now,
        )


def _register_referral_sync(
    referred_user_id: int,
    payload: str,
    users: Dict[str, Any],
    *,
    excluded_ids: Iterable[int] = (),
    now: Optional[datetime] = None,
) -> str:
    """Закрепить реферера один раз. Возвращает результат для аудита/UI."""
    code = parse_referral_payload(payload)
    if not code:
        return "invalid"
    now = now or _now()
    excluded: Set[int] = set(excluded_ids)
    data = _load()
    referrer_id = _safe_user_id(data["codes"].get(code))
    if not referrer_id or referrer_id == referred_user_id or referrer_id in excluded:
        return "rejected"
    referrer_row = users.get(str(referrer_id))
    referred_row = users.get(str(referred_user_id))
    if (
        not isinstance(referrer_row, dict)
        or not isinstance(referred_row, dict)
        or _is_blocked_or_internal(referrer_row)
        or _is_blocked_or_internal(referred_row)
    ):
        return "rejected"
    referred_key = str(referred_user_id)
    if referred_key in data["referrals"]:
        return "already_registered"

    cutoff = now - timedelta(days=1)
    recent = 0
    for item in data["referrals"].values():
        if not isinstance(item, dict) or item.get("referrer_id") != referrer_id:
            continue
        joined = _parse_datetime(item.get("joined_at"))
        if joined and joined >= cutoff:
            recent += 1
    if recent >= MAX_NEW_REFERRALS_PER_DAY:
        return "rate_limited"

    data["referrals"][referred_key] = {
        "referred_user_id": referred_user_id,
        "referrer_id": referrer_id,
        "code": code,
        "status": "pending",
        "joined_at": _iso(now),
        "active_days": [],
        "qualified_at": None,
    }
    _save(data)
    return "registered"


def _parse_datetime(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (TypeError, ValueError):
        return None


def _qualified_in_window(data: Dict[str, Any], referrer_id: int, now: datetime) -> int:
    cutoff = now - timedelta(days=30)
    return sum(
        1
        for item in data["referrals"].values()
        if isinstance(item, dict)
        and item.get("referrer_id") == referrer_id
        and item.get("status") == "qualified"
        and (_parse_datetime(item.get("qualified_at")) or now) >= cutoff
    )


async def record_activity(
    referred_user_id: int,
    users: Dict[str, Any],
    *,
    excluded_ids: Iterable[int] = (),
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Записать активный день и один раз выдать reward за квалификацию."""
    async with referrals_lock:
        return await run_storage_io(
            _record_activity_sync,
            referred_user_id,
            users,
            excluded_ids=excluded_ids,
            now=now,
        )


def _record_activity_sync(
    referred_user_id: int,
    users: Dict[str, Any],
    *,
    excluded_ids: Iterable[int] = (),
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Записать активный день и один раз выдать reward за квалификацию."""
    now = now or _now()
    excluded: Set[int] = set(excluded_ids)
    data = _load()
    item = data["referrals"].get(str(referred_user_id))
    if not isinstance(item, dict) or item.get("status") != "pending":
        return None
    referrer_id = _safe_user_id(item.get("referrer_id"))
    if not referrer_id or referrer_id in excluded:
        item["status"] = "rejected"
        _save(data)
        return "rejected"
    referrer_row = users.get(str(referrer_id))
    referred_row = users.get(str(referred_user_id))
    if (
        not isinstance(referrer_row, dict)
        or not isinstance(referred_row, dict)
        or _is_blocked_or_internal(referrer_row)
        or _is_blocked_or_internal(referred_row)
    ):
        item["status"] = "rejected"
        _save(data)
        return "rejected"

    day = now.astimezone(MSK).date().isoformat()
    active_days = item.setdefault("active_days", [])
    changed = False
    if day not in active_days:
        active_days.append(day)
        changed = True
    joined = _parse_datetime(item.get("joined_at"))
    old_enough = bool(joined and now - joined >= timedelta(hours=MIN_AGE_HOURS))
    if len(active_days) < MIN_ACTIVE_DAYS or not old_enough:
        if changed:
            _save(data)
        return "pending"

    if _qualified_in_window(data, referrer_id, now) >= MAX_REWARDED_REFERRALS_PER_30_DAYS:
        item["status"] = "qualified_capped"
        _save(data)
        return "qualified_capped"

    item["status"] = "qualified"
    item["qualified_at"] = _iso(now)
    event_id = f"activation:{referred_user_id}"
    if not any(entry.get("id") == event_id for entry in data["ledger"] if isinstance(entry, dict)):
        referrer = data["referrers"].setdefault(
            str(referrer_id), {"user_id": referrer_id, "balance": 0}
        )
        referrer["balance"] = int(referrer.get("balance") or 0) + ACTIVATION_REWARD_POINTS
        data["ledger"].append(
            {
                "id": event_id,
                "user_id": referrer_id,
                "amount": ACTIVATION_REWARD_POINTS,
                "kind": "referral_activation",
                "referral_user_id": referred_user_id,
                "created_at": _iso(now),
            }
        )
    _save(data)
    return "qualified"


async def get_dashboard(user_id: int, users: Dict[str, Any], bot_username: str) -> Dict[str, Any]:
    code = await get_or_create_code(user_id)
    async with referrals_lock:
        data = await run_storage_io(_load)
        referrer = data["referrers"].get(str(user_id), {})
        referrals = [
            item
            for item in data["referrals"].values()
            if isinstance(item, dict) and item.get("referrer_id") == user_id
        ]
        redemptions = [
            item
            for item in data["redemptions"]
            if isinstance(item, dict) and item.get("user_id") == user_id
        ]
        return {
            "code": code,
            "link": f"https://t.me/{bot_username.lstrip('@')}?start={REFERRAL_PAYLOAD_PREFIX}{code}",
            "balance": int(referrer.get("balance") or 0),
            "pending": sum(1 for item in referrals if item.get("status") == "pending"),
            "qualified": sum(1 for item in referrals if item.get("status") == "qualified"),
            "total": len(referrals),
            "min_active_days": MIN_ACTIVE_DAYS,
            "min_age_hours": MIN_AGE_HOURS,
            "redemptions": redemptions[-3:],
            "rewards": [dict({"id": reward_id}, **reward) for reward_id, reward in REWARD_CATALOG.items()],
        }


async def redeem_reward(user_id: int, reward_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Списать баллы и создать заявку. Повторный callback не спишет их второй раз."""
    async with referrals_lock:
        return await run_storage_io(_redeem_reward_sync, user_id, reward_id, now=now)


def _redeem_reward_sync(user_id: int, reward_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Списать баллы и создать заявку. Повторный callback не спишет их второй раз."""
    reward = REWARD_CATALOG.get(str(reward_id))
    if not reward:
        return {"ok": False, "reason": "unknown_reward"}
    now = now or _now()
    data = _load()
    referrer = data["referrers"].get(str(user_id))
    if not isinstance(referrer, dict):
        return {"ok": False, "reason": "not_referrer"}
    balance = int(referrer.get("balance") or 0)
    if balance < int(reward["cost"]):
        return {"ok": False, "reason": "insufficient", "balance": balance, "cost": reward["cost"]}
    redemption_id = uuid.uuid4().hex[:12]
    referrer["balance"] = balance - int(reward["cost"])
    data["ledger"].append(
        {
            "id": f"redemption:{redemption_id}",
            "user_id": user_id,
            "amount": -int(reward["cost"]),
            "kind": "reward_redemption",
            "redemption_id": redemption_id,
            "created_at": _iso(now),
        }
    )
    redemption = {
        "id": redemption_id,
        "user_id": user_id,
        "reward_id": reward_id,
        "title": reward["title"],
        "cost": int(reward["cost"]),
        "fulfillment": reward["fulfillment"],
        "status": "pending",
        "created_at": _iso(now),
    }
    data["redemptions"].append(redemption)
    _save(data)
    return {"ok": True, "redemption": redemption, "balance": referrer["balance"]}
