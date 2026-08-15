"""Загрузка VIP-контента колод из data/vip/decks.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from integrations.json_storage import load_json, save_json

_BASE = Path(__file__).resolve().parent.parent
DECKS_FILE = _BASE / "data" / "vip" / "decks.json"
PDF_DIR = _BASE / "data" / "vip" / "pdf"
PDF_FILE_IDS_FILE = _BASE / "data" / "vip" / "pdf_file_ids.json"

# Локальные имена PDF (положить в data/vip/pdf/)
PDF_TEN_DARK = "ten_dushi_book.pdf"
PDF_TEN_LIGHT = "ten_dushi_book_light.pdf"


def _load_pdf_file_ids_raw() -> Dict[str, str]:
    data = load_json(PDF_FILE_IDS_FILE, {})
    return {k: v for k, v in data.items() if isinstance(v, str) and v}


def get_pdf_file_id(variant: str) -> Optional[str]:
    """Telegram file_id для мгновенной повторной отправки (как SmartBot)."""
    return _load_pdf_file_ids_raw().get(variant)


def save_pdf_file_id(variant: str, file_id: str) -> None:
    data = _load_pdf_file_ids_raw()
    data[variant] = file_id
    save_json(PDF_FILE_IDS_FILE, data, trailing_newline=True)


@lru_cache(maxsize=1)
def load_decks_data() -> Dict[str, Any]:
    if not DECKS_FILE.is_file():
        return {}
    return json.loads(DECKS_FILE.read_text(encoding="utf-8"))


def no_access_html() -> str:
    return load_decks_data().get("no_access_html") or ""


def welcome_html() -> str:
    return load_decks_data().get("welcome_html") or ""


def deck_menu_html() -> str:
    return load_decks_data().get("deck_menu_html") or ""


def vip_home_html() -> str:
    """Приветствие VIP + выбор колоды в одном экране."""
    welcome = welcome_html().rstrip()
    menu = deck_menu_html().strip()
    if welcome and menu:
        return f"{welcome}\n\n{menu}"
    return welcome or menu


def deck_keys() -> List[str]:
    decks = load_decks_data().get("decks") or {}
    return list(decks.keys())


def get_deck(deck_id: str) -> Optional[Dict[str, Any]]:
    decks = load_decks_data().get("decks") or {}
    return decks.get(deck_id)


def deck_menu_keyboard() -> InlineKeyboardMarkup:
    decks = load_decks_data().get("decks") or {}
    rows = []
    labels = {
        "iskry": "Искры Женской Божественности",
        "ten": "Тень души",
        "kristally": "Кристаллы Атлантиды (Крайона)",
    }
    for key in ("ten", "iskry", "kristally"):
        if key in decks:
            rows.append(
                [
                    InlineKeyboardButton(
                        labels.get(key, key),
                        callback_data=f"vip:deck:{key}",
                    )
                ]
            )
    return InlineKeyboardMarkup(rows)


def deck_sections_keyboard(deck_id: str) -> InlineKeyboardMarkup:
    deck = get_deck(deck_id)
    rows: List[List[InlineKeyboardButton]] = []
    if deck:
        for sec in deck.get("sections") or []:
            sid = sec.get("id", "")
            title = sec.get("title", sid)
            if deck_id == "ten" and "pdf" in title.casefold():
                continue
            rows.append(
                [
                    InlineKeyboardButton(
                        title,
                        callback_data=f"vip:sec:{deck_id}:{sid}",
                    )
                ]
            )
        if deck_id == "ten":
            rows.append(
                [
                    InlineKeyboardButton(
                        "🟢 Книга PDF (тёмная)",
                        callback_data="vip:pdf:ten:dark",
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        "🟢 Книга PDF (светлая)",
                        callback_data="vip:pdf:ten:light",
                    )
                ]
            )
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="vip:decks")])
    return InlineKeyboardMarkup(rows)


def section_back_keyboard(deck_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Назад", callback_data=f"vip:deck:{deck_id}")]]
    )


def kristally_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Назад", callback_data="vip:decks")]]
    )


def get_section_html(deck_id: str, section_id: str) -> Optional[str]:
    deck = get_deck(deck_id)
    if not deck:
        return None
    for sec in deck.get("sections") or []:
        if sec.get("id") == section_id:
            return sec.get("html")
    return None


def pdf_local_path(variant: str) -> Path:
    name = PDF_TEN_DARK if variant == "dark" else PDF_TEN_LIGHT
    return PDF_DIR / name


def split_html_message(text: str, limit: int = 4000) -> List[str]:
    """Разбить длинный HTML на части ≤ limit символов."""
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    buf: List[str] = []
    size = 0
    for line in text.split("\n"):
        chunk = line + "\n"
        if size + len(chunk) > limit and buf:
            parts.append("".join(buf).rstrip())
            buf = [chunk]
            size = len(chunk)
        else:
            buf.append(chunk)
            size += len(chunk)
    if buf:
        parts.append("".join(buf).rstrip())
    return parts
