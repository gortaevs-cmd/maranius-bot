"""Ангельские знаки (локальные CSV)."""

from __future__ import annotations

import asyncio
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

_ANGELIC_DIR = Path(__file__).resolve().parent.parent / "data" / "angelic"
_NUMS_CSV = _ANGELIC_DIR / "nums.csv"
_CLOCKS_CSV = _ANGELIC_DIR / "clocks.csv"
_UNKNOWN_CSV = _ANGELIC_DIR / "unknown_angelic.csv"

_nums: Optional[Dict[str, str]] = None
_clocks: Optional[Dict[str, str]] = None
_unknown_lock = asyncio.Lock()


def _load_csv_map(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not path.is_file():
        return result
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh, delimiter=";"):
            if len(row) < 2:
                continue
            key = row[0].strip()
            meaning = row[1].strip()
            if key:
                result[key] = meaning
    return result


def _ensure_angelic_loaded() -> None:
    global _nums, _clocks
    if _nums is None:
        _nums = _load_csv_map(_NUMS_CSV)
    if _clocks is None:
        _clocks = _load_csv_map(_CLOCKS_CSV)


def is_angelic_input(text: str) -> bool:
    """Число (1–3 цифры), время или 1111 — как в сценарии EWASML."""
    raw = (text or "").strip()
    if not raw:
        return False
    if re.fullmatch(r"\d{4}", raw):
        return True
    if re.fullmatch(r"\d+", raw):
        return len(raw) in (1, 2, 3)
    if re.fullmatch(r"\d{1,2}[:. ]\d{1,2}", raw):
        return True
    return False


def normalize_angelic_key(raw: str) -> str:
    """Привести ввод к ключу nums/clocks (1111 → 11:11, 11.11 → 11:11)."""
    s = (raw or "").strip()
    if re.fullmatch(r"\d{4}", s):
        return f"{s[:2]}:{s[2:]}"
    if re.fullmatch(r"\d{1,2}[:. ]\d{1,2}", s):
        s = re.sub(r"[. ]", ":", s)
        hour, minute = s.split(":", 1)
        return f"{int(hour):02d}:{int(minute):02d}"
    return s


def lookup_angelic_sign(key: str) -> Tuple[Optional[str], str]:
    """
    Расшифровка по локальным CSV.
    Returns: (meaning or None, normalized_key)
    """
    _ensure_angelic_loaded()
    normalized = normalize_angelic_key(key)
    if ":" in normalized:
        meaning = (_clocks or {}).get(normalized)
    else:
        meaning = (_nums or {}).get(normalized)
    return meaning, normalized


def unknown_angelic_path() -> Path:
    return _UNKNOWN_CSV


async def log_unknown_angelic(
    raw_input: str,
    normalized: str,
    *,
    user_id: int,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """Записать неизвестную комбинацию в inbox."""
    from integrations import inbox as inbox_mod

    return await inbox_mod.add_entry(
        entry_type="unknown_angel",
        user_id=user_id,
        username=username,
        text=raw_input.strip(),
        meta={"normalized": normalized},
    )
