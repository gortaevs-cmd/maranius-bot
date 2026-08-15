#!/usr/bin/env python3
"""Извлечь каталог карт и MD-инструкцию из PDF «Тень души» (тёмная книга)."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF = ROOT / "data" / "vip" / "pdf" / "ten_dushi_book.pdf"
OUT_DIR = ROOT / "data" / "vip" / "ten_dushi"

FOOTER_RE = re.compile(
    r"Maranius El Shaddai\s+Тень Души ►\s*(\d+)\s*$",
    re.MULTILINE,
)
PAGE_MARK_RE = re.compile(r"^\s*--\s*\d+\s+of\s+\d+\s*--\s*$", re.MULTILINE)
TOC_CARD_RE = re.compile(
    r"^[Кк]арта:\s*(.+?)\s*$",
    re.MULTILINE,
)
SECTION_HEADERS = {
    "старшие арканы",
    "кубки",
    "пентакли",
    "мечи",
    "жезлы",
    "дополнительные материалы",
}


def _clean_text(text: str) -> str:
    text = PAGE_MARK_RE.sub("\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # склеенные предложения без пробела после точки/запятой перед заглавной кириллицей
    text = re.sub(r"([.!?])([А-ЯЁ])", r"\1 \2", text)
    text = re.sub(r"([a-zа-яё])([А-ЯЁ])", r"\1 \2", text)
    return text.strip()


def _parse_toc(text: str) -> Tuple[List[Dict[str, str]], str]:
    """Оглавление из первых страниц до первой карты."""
    start = text.find("Старшие арканы")
    if start < 0:
        start = 0
    end = text.find("THE GATES OF SHADOWS")
    if end < 0:
        end = FOOTER_RE.search(text).start() if FOOTER_RE.search(text) else len(text)
    toc_raw = text[start:end]
    entries: List[Dict[str, str]] = []
    section = "Старшие арканы"
    for line in toc_raw.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"\d+", line):
            continue
        low = line.casefold().rstrip(":")
        if low in SECTION_HEADERS or line.rstrip(":") in (
            "Содержание колоды",
            "дополнительные материалы",
        ):
            if low in SECTION_HEADERS:
                section = line.rstrip(":").capitalize()
                if section == "Жезлы":
                    section = "Жезлы"
            continue
        m = TOC_CARD_RE.match(line)
        if m:
            raw = m.group(1).strip()
            title_en, title_ru = _split_bilingual_title(raw)
            entries.append(
                {
                    "section": section,
                    "title_raw": raw,
                    "title_en": title_en,
                    "title_ru": title_ru,
                }
            )
    return entries, toc_raw


def _split_bilingual_title(raw: str) -> Tuple[str, str]:
    if " — " in raw:
        en, ru = raw.split(" — ", 1)
        return en.strip(), ru.strip()
    if ": " in raw and re.match(r"^The ", raw):
        en, ru = raw.split(": ", 1)
        return en.strip(), ru.strip()
    if re.search(r"[A-Za-z]", raw):
        return raw.strip(), ""
    return "", raw.strip()


def _extract_cards(text: str) -> List[Dict[str, Any]]:
    matches = list(FOOTER_RE.finditer(text))
    cards: List[Dict[str, Any]] = []
    prev_end = 0
    for m in matches:
        block = text[prev_end : m.start()].strip()
        prev_end = m.end()
        num = int(m.group(1))
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        # убрать артефакты вроде "B r a n d o n , M i s s o u r i"
        lines = [ln for ln in lines if not re.fullmatch(r"([A-Za-z]\s+){3,}", ln)]
        title_lines: List[str] = []
        body_lines: List[str] = []
        # заголовок карты — последние 1–4 строки перед footer (EN/RU)
        for i, ln in enumerate(lines):
            if re.match(r"^THE [A-Z\s]+$", ln) or ln.startswith("THE "):
                body_lines = lines[i:]
                break
        if not body_lines and lines:
            # первая карта (Gates) может начинаться с русского текста
            if "Визуальный образ" in block or "Описание карты" in block:
                body_lines = lines
            else:
                body_lines = lines
        # title from tail: lines with — or : or (шут) before body end marker
        tail = []
        for ln in reversed(lines):
            if ln in body_lines[:3] if body_lines else []:
                break
            if re.match(r"^THE [A-Z]", ln) and "—" not in ln and ":" not in ln:
                break
            tail.insert(0, ln)
            if "—" in ln or ": " in ln or re.search(r"\([а-яА-Я]", ln):
                # grab preceding THE ... lines
                idx = lines.index(ln)
                pre = []
                j = idx - 1
                while j >= 0 and (
                    re.match(r"^THE [A-Z\s]+$", lines[j])
                    or lines[j].startswith("THE ")
                ):
                    pre.insert(0, lines[j])
                    j -= 1
                title_lines = pre + [ln]
                if j + 1 < idx:
                    mid = lines[j + 1 : idx]
                    if mid and not mid[0].startswith("THE "):
                        title_lines = mid + title_lines
                break
        title_raw = " ".join(title_lines).strip() if title_lines else ""
        if not title_raw and tail:
            title_raw = " ".join(tail[-3:]).strip()
        title_en, title_ru = _split_bilingual_title(title_raw.replace("\n", " "))
        body = "\n".join(body_lines).strip()
        # убрать дубли заголовка из тела
        if title_lines:
            for tl in title_lines:
                body = body.replace(tl, "", 1).strip()
        body = re.sub(
            r"^(?:THE [A-Z][A-Z\s]*\n?)+",
            "",
            body,
            count=1,
            flags=re.MULTILINE,
        ).strip()
        cards.append(
            {
                "number": num,
                "title_raw": title_raw or None,
                "title_en": title_en or None,
                "title_ru": title_ru or None,
                "body": _clean_text(body),
            }
        )
    return cards


def _merge_toc_and_cards(
    toc: List[Dict[str, str]], cards: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Сопоставить секции из оглавления с картами по порядку."""
    merged: List[Dict[str, Any]] = []
    for i, card in enumerate(cards):
        entry = dict(card)
        if i < len(toc):
            t = toc[i]
            entry["section"] = t["section"]
            entry["title_raw"] = t.get("title_raw") or entry.get("title_raw")
            entry["title_en"] = t.get("title_en") or ""
            entry["title_ru"] = t.get("title_ru") or ""
            if not entry["title_en"] and not entry["title_ru"] and entry.get("title_raw"):
                entry["title_ru"] = entry["title_raw"]
        else:
            entry["section"] = entry.get("section") or "Дополнительно"
        merged.append(entry)
    return merged


def _extract_intro(text: str) -> str:
    m = re.search(
        r"(Дорогой искатель тайн,.+?Maranius El Shaddai)\s*\nвступительная речь",
        text,
        re.DOTALL,
    )
    return _clean_text(m.group(1)) if m else ""


def _extract_spreads(text: str) -> str:
    start = text.find("Варианты раскладов")
    end = text.find("Дорогой искатель тайн,")
    if start < 0 or end < 0:
        return ""
    chunk = text[start:end]
    chunk = re.sub(r"Maranius El Shaddai\s*$", "", chunk, flags=re.MULTILINE)
    return _clean_text(chunk)


def _card_heading(card: Dict[str, Any]) -> str:
    en = card.get("title_en") or ""
    ru = card.get("title_ru") or ""
    if en and ru:
        return f"{en} — {ru}"
    return card.get("title_raw") or en or ru or f"Карта {card.get('number')}"


def _slug(text: str) -> str:
    s = text.casefold().replace(" ", "-")
    s = re.sub(r"[^a-z0-9а-яё\-]", "", s)
    return s[:80] or "card"


def build_markdown(
    cards: List[Dict[str, Any]], intro: str, spreads: str, source_pdf: str
) -> str:
    lines = [
        "# Колода «Тень души» — инструкция",
        "",
        f"*Извлечено из PDF ({source_pdf}), {date.today().isoformat()}*",
        "",
        "## Оглавление",
        "",
    ]
    current_section = None
    for card in cards:
        sec = card.get("section") or "Карты"
        if sec != current_section:
            current_section = sec
            lines.append(f"### {sec}")
            lines.append("")
        anchor = _slug(_card_heading(card))
        card["anchor"] = anchor
        lines.append(f"- [{_card_heading(card)}](#{anchor})")
    lines.append("")
    if intro:
        lines.extend(["---", "", "## Вступительная речь", "", intro, ""])
    lines.extend(["---", "", "## Карты", ""])
    current_section = None
    for card in cards:
        sec = card.get("section") or "Карты"
        if sec != current_section:
            current_section = sec
            lines.extend([f"### {sec}", ""])
        lines.extend([f"#### {_card_heading(card)} {{#{card['anchor']}}}", ""])
        if card.get("body"):
            lines.append(card["body"])
        lines.append("")
    if spreads:
        lines.extend(["---", "", "## Варианты раскладов", "", spreads, ""])
    lines.extend(
        [
            "---",
            "",
            "*Maranius El Shaddai · колода «Тень души»*",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    if not pdf_path.is_file():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    doc = fitz.open(pdf_path)
    text = _clean_text("\n".join(page.get_text() for page in doc))

    toc, _ = _parse_toc(text)
    cards_raw = _extract_cards(text)
    cards = _merge_toc_and_cards(toc, cards_raw)
    intro = _extract_intro(text)
    spreads = _extract_spreads(text)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog = {
        "deck_id": "ten",
        "deck_title": "Тень души",
        "source_pdf": pdf_path.name,
        "extracted_at": date.today().isoformat(),
        "cards_count": len(cards),
        "intro": intro,
        "spreads_text": spreads,
        "cards": cards,
    }
    (OUT_DIR / "cards_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md = build_markdown(cards, intro, spreads, pdf_path.name)
    (OUT_DIR / "ИНСТРУКЦИЯ_Тень_души.md").write_text(md, encoding="utf-8")

    readme = f"""# «Тень души» — текстовый каталог

Извлечено из тёмной PDF-книги (`{pdf_path.name}`).

| Файл | Назначение |
|------|------------|
| `cards_catalog.json` | машиночитаемый каталог ({len(cards)} карт) |
| `ИНСТРУКЦИЯ_Тень_души.md` | инструкция с оглавлением для людей |

Перегенерация:

```bash
python3 scripts/extract_ten_dushi_from_pdf.py
```

PDF для VIP по-прежнему в `data/vip/pdf/`. Эти файлы — editable-копия текстов.
"""
    (OUT_DIR / "README.md").write_text(readme, encoding="utf-8")

    print(f"OK: {len(cards)} cards -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
