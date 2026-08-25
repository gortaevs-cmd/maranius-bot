"""Карта дня, кристалл и кубик: каталог/значение, лимит 1×/сутки MSK."""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict
from urllib.parse import quote
from zoneinfo import ZoneInfo

_BASE = Path(__file__).resolve().parent.parent
CATALOG_FILE = _BASE / "data" / "daily_practice_catalog.json"
MSK = ZoneInfo("Europe/Moscow")

# Страницы карт публикуются статическим сайтом по /practice/podskazki/<id>.
DEFAULT_CARD_BASE_URL = "https://maranius.ru/practice/podskazki"
# Карточки кристаллов опубликованы на одной странице; slug каждой карточки — её якорь.
DEFAULT_CRYSTAL_BASE_URL = "https://maranius.ru/themes/kristally"


class PullRecord(TypedDict):
    slug: str
    title: str
    url: str


class DiceRecord(TypedDict):
    value: int


class PracticeState(TypedDict):
    date_local: str
    card: Optional[PullRecord]
    crystal: Optional[PullRecord]
    dice: Optional[DiceRecord]


def local_date_str(*, timezone_str: Optional[str] = None, now: Optional[datetime] = None) -> str:
    """Локальная дата по часовому поясу пользователя; при ошибке — Москва."""
    try:
        tz = ZoneInfo(timezone_str) if timezone_str else MSK
    except (KeyError, ValueError):
        tz = MSK
    dt = now or datetime.now(tz)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt.date().isoformat()


def msk_date_str(*, now: Optional[datetime] = None) -> str:
    """Совместимый helper для старых вызовов."""
    return local_date_str(timezone_str="Europe/Moscow", now=now)


def card_base_url() -> str:
    return (os.getenv("CARD_OF_DAY_BASE_URL") or DEFAULT_CARD_BASE_URL).rstrip("/")


def crystal_base_url() -> str:
    return (os.getenv("CRYSTAL_OF_DAY_BASE_URL") or DEFAULT_CRYSTAL_BASE_URL).rstrip("/")


def crystal_url(slug: str) -> str:
    return f"{crystal_base_url()}/#{quote(slug, safe='')}"


def _load_catalog_raw() -> Dict[str, List[Dict[str, str]]]:
    if not CATALOG_FILE.is_file():
        return {"cards": [], "crystals": []}
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"cards": [], "crystals": []}
    return {
        "cards": [x for x in (data.get("cards") or []) if isinstance(x, dict)],
        "crystals": [x for x in (data.get("crystals") or []) if isinstance(x, dict)],
    }


def _pick_item(pool: List[Dict[str, str]], *, kind: str) -> Optional[PullRecord]:
    items = [
        x
        for x in pool
        if isinstance(x.get("slug"), str) and x["slug"].strip()
    ]
    if not items:
        return None
    item = secrets.choice(items)
    slug = item["slug"].strip()
    title = (item.get("title") or slug).strip()
    if kind == "card":
        url = f"{card_base_url()}/{quote(slug, safe='')}"
    else:
        url = crystal_url(slug)
    return PullRecord(slug=slug, title=title, url=url)


def pick_random_card() -> Optional[PullRecord]:
    return _pick_item(_load_catalog_raw()["cards"], kind="card")


def pick_random_crystal() -> Optional[PullRecord]:
    return _pick_item(_load_catalog_raw()["crystals"], kind="crystal")


def normalize_practice(raw: Any, *, today_local: Optional[str] = None, timezone_str: Optional[str] = None, today_msk: Optional[str] = None) -> PracticeState:
    """Сбросить запись, если дата не сегодня в зоне пользователя."""
    today = today_local or today_msk or local_date_str(timezone_str=timezone_str)
    empty: PracticeState = {"date_local": today, "card": None, "crystal": None, "dice": None}
    if not isinstance(raw, dict):
        return empty
    stored_date = raw.get("date_local") or raw.get("date_msk")
    if stored_date != today:
        return empty
    state: PracticeState = {"date_local": today, "card": None, "crystal": None, "dice": None}
    for key in ("card", "crystal"):
        entry = raw.get(key)
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("slug"), str)
            and isinstance(entry.get("url"), str)
        ):
            slug = entry["slug"]
            url = entry["url"]
            if key == "crystal" and "?crystal=" in url and url.endswith("#kristall"):
                url = crystal_url(slug)
            state[key] = PullRecord(  # type: ignore[literal-required]
                slug=slug,
                title=str(entry.get("title") or slug),
                url=url,
            )
    dice_entry = raw.get("dice")
    if isinstance(dice_entry, dict):
        raw_value = dice_entry.get("value")
        if isinstance(raw_value, int) and 1 <= raw_value <= 6:
            state["dice"] = DiceRecord(value=raw_value)
    return state


def practice_from_user(user_record: Optional[Dict[str, Any]]) -> PracticeState:
    if not user_record:
        return normalize_practice(None)
    return normalize_practice(user_record.get("daily_practice"), timezone_str=user_record.get("timezone"))


def apply_card_pull(state: PracticeState, pull: PullRecord) -> PracticeState:
    return PracticeState(
        date_local=state["date_local"],
        card=pull,
        crystal=state.get("crystal"),
        dice=state.get("dice"),
    )


def apply_crystal_pull(state: PracticeState, pull: PullRecord) -> PracticeState:
    return PracticeState(
        date_local=state["date_local"],
        card=state.get("card"),
        crystal=pull,
        dice=state.get("dice"),
    )


def apply_dice_roll(state: PracticeState, value: int) -> PracticeState:
    face = value if 1 <= value <= 6 else 1
    return PracticeState(
        date_local=state["date_local"],
        card=state.get("card"),
        crystal=state.get("crystal"),
        dice=DiceRecord(value=face),
    )
