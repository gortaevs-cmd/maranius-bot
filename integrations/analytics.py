"""События активности и агрегаты для отчётов."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

from integrations.json_storage import load_json, save_json

_LOCK = asyncio.Lock()
_BASE = Path(__file__).resolve().parent.parent
EVENTS_FILE = _BASE / "data" / "activity_events.json"
AGGREGATES_FILE = _BASE / "data" / "activity_aggregates.json"
RETENTION_DAYS = 90
MSK = pytz.timezone("Europe/Moscow")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path, default: Any) -> Any:
    return load_json(path, default)


def _save_json(path: Path, data: Any) -> None:
    save_json(path, data, trailing_newline=True)


async def log_section(user_id: int, section: str) -> None:
    """section: today, vip, store, more, angel, card, crystal, dice, weather, moon, …"""
    async with _LOCK:
        store = _load_json(EVENTS_FILE, {"events": []})
        events: List[Dict[str, Any]] = store.get("events", [])
        if not isinstance(events, list):
            events = []
        events.append(
            {
                "ts": _now_iso(),
                "user_id": user_id,
                "section": section,
            }
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        pruned: List[Dict[str, Any]] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            try:
                dt = datetime.fromisoformat(str(ev.get("ts", "")).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if dt >= cutoff:
                pruned.append(ev)
        store["events"] = pruned[-50000:]
        _save_json(EVENTS_FILE, store)


def _events_in_period(days: int) -> List[Dict[str, Any]]:
    store = _load_json(EVENTS_FILE, {"events": []})
    events = store.get("events", [])
    if not isinstance(events, list):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        try:
            dt = datetime.fromisoformat(str(ev.get("ts", "")).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if dt >= cutoff:
            out.append(ev)
    return out


def top_sections(days: int = 7, limit: int = 5) -> List[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for ev in _events_in_period(days):
        sec = str(ev.get("section") or "")
        if sec:
            counter[sec] += 1
    return counter.most_common(limit)


def period_stats(days: int) -> Dict[str, Any]:
    events = _events_in_period(days)
    sections: Counter[str] = Counter()
    users: set[int] = set()
    for ev in events:
        sections[str(ev.get("section") or "unknown")] += 1
        try:
            users.add(int(ev.get("user_id")))
        except (ValueError, TypeError):
            pass
    return {
        "events": len(events),
        "unique_users": len(users),
        "top_sections": sections.most_common(5),
    }


async def rollup_weekly() -> None:
    """Сохранить недельный агрегат."""
    async with _LOCK:
        ag = _load_json(AGGREGATES_FILE, {"weekly": [], "monthly": []})
        week_key = datetime.now(MSK).strftime("%Y-W%W")
        entry = {
            "period": week_key,
            "computed_at": _now_iso(),
            "stats_7d": period_stats(7),
        }
        weekly: List[Dict[str, Any]] = ag.get("weekly", [])
        if not isinstance(weekly, list):
            weekly = []
        weekly = [w for w in weekly if w.get("period") != week_key]
        weekly.append(entry)
        ag["weekly"] = weekly[-52:]
        _save_json(AGGREGATES_FILE, ag)


async def rollup_monthly(year: int, month: int) -> None:
    async with _LOCK:
        ag = _load_json(AGGREGATES_FILE, {"weekly": [], "monthly": []})
        key = f"{year:04d}-{month:02d}"
        entry = {
            "period": key,
            "computed_at": _now_iso(),
            "stats_30d": period_stats(30),
        }
        monthly: List[Dict[str, Any]] = ag.get("monthly", [])
        if not isinstance(monthly, list):
            monthly = []
        monthly = [m for m in monthly if m.get("period") != key]
        monthly.append(entry)
        ag["monthly"] = monthly[-24:]
        _save_json(AGGREGATES_FILE, ag)
