"""Тексты кубика: 30 вариантов на грань, ротация по календарным суткам MSK."""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

_BASE = Path(__file__).resolve().parent.parent
DICE_MESSAGES_FILE = _BASE / "data" / "dice_messages.json"
MSK = ZoneInfo("Europe/Moscow")
VARIANTS_PER_FACE = 30

DICE_TITLES: Dict[int, str] = {
    1: "Единица",
    2: "Двойка",
    3: "Тройка",
    4: "Четвёрка",
    5: "Пятёрка",
    6: "Шестёрка",
}

# Fallback, если JSON недоступен (первый вариант каждой грани)
_FALLBACK_BODIES: Dict[int, str] = {
    1: "Сегодня хороший день для одного ясного намерения. Сфокусируйся на главном.",
    2: "Ищи баланс и партнёрство. Мягкий диалог откроет больше, чем спор.",
    3: "Твори, выражай себя, делись светом. Маленький шаг творчества принесёт радость.",
    4: "Заземление и порядок. Наведи порядок в одном деле — опора вернётся.",
    5: "Перемены и движение. Будь гибк(а), но помни о внутреннем центре.",
    6: "Забота и гармония дома. Побудь с близкими или с собой — без спешки.",
}


@lru_cache(maxsize=1)
def _load_messages_raw() -> Dict[int, List[str]]:
    if not DICE_MESSAGES_FILE.is_file():
        return {}
    try:
        data = json.loads(DICE_MESSAGES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    raw = data.get("messages") or {}
    out: Dict[int, List[str]] = {}
    for key, items in raw.items():
        if not isinstance(items, list):
            continue
        texts = [s for s in items if isinstance(s, str) and s.strip()]
        if texts:
            out[int(key)] = texts
    return out


def daily_variant_index(*, now: Optional[datetime] = None) -> int:
    """Индекс 0…29 для текущих календарных суток по Europe/Moscow."""
    dt = now or datetime.now(MSK)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    else:
        dt = dt.astimezone(MSK)
    return dt.date().toordinal() % VARIANTS_PER_FACE


def get_dice_message_html(value: int, *, now: Optional[datetime] = None) -> str:
    """HTML-текст подсказки дня для выпавшей грани кубика (1–6)."""
    face = value if 1 <= value <= 6 else 1
    title = DICE_TITLES[face]
    idx = daily_variant_index(now=now)
    pool = _load_messages_raw().get(face) or []
    if pool:
        body = pool[idx % len(pool)]
    else:
        body = _FALLBACK_BODIES[face]
    return f"🎲 <b>{title}.</b> {body}"
