#!/usr/bin/env python3
"""Собрать PDF-инструкцию из MD «Тень души» (текст после extract_ten_dushi_from_pdf.py)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple, Union

try:
    from fpdf import FPDF
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Нужен fpdf2: pip install fpdf2") from exc

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MD = ROOT / "data" / "vip" / "ten_dushi" / "ИНСТРУКЦИЯ_Тень_души.md"
DEFAULT_PDF = ROOT / "data" / "vip" / "ten_dushi" / "ИНСТРУКЦИЯ_Тень_души.pdf"

FONT_REGULAR = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
FONT_ITALIC = Path("/System/Library/Fonts/Supplemental/Arial Italic.ttf")

HEADER_RE = re.compile(r"^(#{1,4})\s+(.*)$")
ANCHOR_RE = re.compile(r"\s*\{#.+?\}\s*$")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
ORDERED_RE = re.compile(r"^\d+\.\s")
HR_RE = re.compile(r"^-{3,}\s*$")
COLON_SPLIT_RE = re.compile(r"^([^:\n]{3,72}):\s*(.*)$", re.DOTALL)
BULLET_DASH_RE = re.compile(r"^(.{2,55})\s+—\s+(.+)$")

MAJOR_LABELS = {
    "описание карты",
    "эмоциональная часть",
    "символизм",
    "значение для колоды «тень души»",
    "визуальный образ и символика",
    "значение карты в раскладе",
    "основные интерпретации",
    "совет карты",
    "вопросы для медитации",
    "ключевая энергия карты",
    "расположение карт",
    "визуализация расклада",
    "использование",
    "позиции расклада",
}

Block = Tuple[str, object]


def _clean_header(text: str) -> str:
    text = ANCHOR_RE.sub("", text.strip())
    return LINK_RE.sub(r"\1", text)


def _clean_inline(text: str) -> str:
    text = LINK_RE.sub(r"\1", text)
    if text.startswith("*") and text.endswith("*") and text.count("*") == 2:
        return text[1:-1]
    return text.strip()


def _ends_sentence(line: str) -> bool:
    line = line.rstrip()
    return bool(line) and line[-1] in ".!?"


def _is_major_label(label: str) -> bool:
    return label.casefold().rstrip(":") in MAJOR_LABELS or label.casefold().startswith("расклад")


def _should_start_new_paragraph(prev: str, curr: str) -> bool:
    if not prev:
        return False
    prev = prev.rstrip()
    curr = curr.lstrip()
    if not curr:
        return False
    if _ends_sentence(prev):
        return True
    if prev.endswith(":"):
        return bool(curr)
    # перенос строки из PDF: продолжение, если новая строка с маленькой буквы
    first = curr[0]
    if first.islower() or first in "«\"'(":
        return False
    return False


def _flush_paragraph(acc: List[str], blocks: List[Block]) -> None:
    if not acc:
        return
    text = _clean_inline(" ".join(acc))
    if text:
        blocks.append(("p", text))
    acc.clear()


def _handle_colon_line(line: str, blocks: List[Block], acc: List[str]) -> bool:
    m = COLON_SPLIT_RE.match(line)
    if not m:
        return False
    label, rest = m.group(1).strip(), m.group(2).strip()
    if "," in label or len(label) > 45:
        return False
    if rest and rest[0].islower():
        return False
    if not rest and not _is_major_label(label):
        return False
    if "." in label[:-1]:
        return False

    _flush_paragraph(acc, blocks)
    full_label = f"{label}:"
    if _is_major_label(label):
        blocks.append(("subhead", full_label))
    else:
        blocks.append(("mini", full_label))
    if rest:
        acc.append(rest)
    return True


def parse_md(text: str) -> List[Block]:
    lines = text.splitlines()
    blocks: List[Block] = []
    acc: List[str] = []
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            _flush_paragraph(acc, blocks)
            i += 1
            continue

        if HR_RE.match(line.strip()):
            _flush_paragraph(acc, blocks)
            blocks.append(("hr", ""))
            i += 1
            continue

        hm = HEADER_RE.match(line)
        if hm:
            _flush_paragraph(acc, blocks)
            level = len(hm.group(1))
            blocks.append((f"h{level}", _clean_header(hm.group(2))))
            i += 1
            continue

        if line.startswith("- "):
            _flush_paragraph(acc, blocks)
            items: List[str] = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(_clean_inline(lines[i][2:].strip()))
                i += 1
            blocks.append(("ul", items))
            continue

        if ORDERED_RE.match(line):
            _flush_paragraph(acc, blocks)
            items: List[str] = []
            while i < len(lines) and ORDERED_RE.match(lines[i]):
                items.append(_clean_inline(ORDERED_RE.sub("", lines[i]).strip()))
                i += 1
            blocks.append(("ol", items))
            continue

        if line.strip().startswith("```"):
            _flush_paragraph(acc, blocks)
            i += 1
            code_lines: List[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i].rstrip("\n"))
                i += 1
            if i < len(lines):
                i += 1
            blocks.append(("pre", "\n".join(code_lines)))
            continue

        stripped = line.strip()
        bm = BULLET_DASH_RE.match(stripped)
        if bm and not acc:
            prev_label = str(blocks[-1][1]).casefold() if blocks else ""
            if blocks and blocks[-1][0] in {"subhead", "mini"} and "символ" in prev_label:
                _flush_paragraph(acc, blocks)
                items = [stripped]
                i += 1
                while i < len(lines):
                    nxt = lines[i].strip()
                    if not nxt or HEADER_RE.match(nxt) or HR_RE.match(nxt):
                        break
                    if COLON_SPLIT_RE.match(nxt):
                        m = COLON_SPLIT_RE.match(nxt)
                        if m and _is_major_label(m.group(1)):
                            break
                    if BULLET_DASH_RE.match(nxt):
                        items.append(nxt)
                        i += 1
                        continue
                    # продолжение предыдущего пункта после переноса PDF
                    items[-1] = f"{items[-1]} {_clean_inline(nxt)}"
                    i += 1
                blocks.append(("ul", items))
                continue

        if _handle_colon_line(stripped, blocks, acc):
            i += 1
            continue

        if acc and _should_start_new_paragraph(acc[-1], stripped):
            _flush_paragraph(acc, blocks)

        if re.match(r"^[A-Za-z].+\s—\s*$", stripped):
            i += 1
            continue

        acc.append(stripped)
        i += 1

    _flush_paragraph(acc, blocks)
    return blocks


class TenDushiPDF(FPDF):
    def __init__(self) -> None:
        super().__init__(unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(20, 20, 20)
        self.add_font("Book", "", str(FONT_REGULAR))
        self.add_font("Book", "B", str(FONT_BOLD))
        if FONT_ITALIC.is_file():
            self.add_font("Book", "I", str(FONT_ITALIC))
        self._text_size = 10.5

    def _fit_multi_cell(
        self,
        w: float,
        h: float,
        text: str,
        *,
        style: str = "",
        align: str = "L",
    ) -> None:
        self.set_x(self.l_margin)
        self.set_font("Book", style=style, size=self._text_size)
        self.multi_cell(w, h, text, align=align)

    def render_blocks(self, blocks: Iterable[Block]) -> None:
        self.add_page()
        for kind, payload in blocks:
            if kind == "h1":
                self.ln(2)
                self._text_size = 20
                self._fit_multi_cell(0, 10, str(payload), style="B", align="L")
                self.ln(4)
            elif kind == "h2":
                if self.get_y() > 40:
                    self.ln(8)
                self._text_size = 15
                self._fit_multi_cell(0, 8, str(payload), style="B", align="L")
                self.ln(3)
            elif kind == "h3":
                self.ln(5)
                self._text_size = 12.5
                self._fit_multi_cell(0, 7, str(payload), style="B", align="L")
                self.ln(2)
            elif kind == "h4":
                if self.get_y() > 30:
                    self.add_page()
                self.ln(2)
                self._text_size = 12
                self._fit_multi_cell(0, 7, str(payload), style="B", align="L")
                self.ln(2)
            elif kind == "subhead":
                self.ln(4)
                self._text_size = 11
                self._fit_multi_cell(0, 6, str(payload), style="B", align="L")
                self.ln(1)
            elif kind == "mini":
                self.ln(2.5)
                self._text_size = 10.5
                self._fit_multi_cell(0, 6, str(payload), style="B", align="L")
                self.ln(0.5)
            elif kind == "p":
                self._text_size = 10.5
                self._fit_multi_cell(0, 6, str(payload))
                self.ln(2.5)
            elif kind == "ul":
                self._text_size = 10.5
                self.ln(1)
                for item in payload:  # type: ignore[union-attr]
                    bm = BULLET_DASH_RE.match(str(item))
                    text = f"{bm.group(1)} — {bm.group(2)}" if bm else str(item)
                    self._fit_multi_cell(0, 6, f"    •  {text}")
                    self.ln(1)
                self.ln(1.5)
            elif kind == "ol":
                self._text_size = 10.5
                self.ln(1)
                for n, item in enumerate(payload, 1):  # type: ignore[union-attr]
                    self._fit_multi_cell(0, 6, f"  {n}.  {item}")
                    self.ln(1.5)
                self.ln(1.5)
            elif kind == "pre":
                self.ln(2)
                self._text_size = 9.5
                self._fit_multi_cell(0, 5, str(payload), align="L")
                self.ln(3)
            elif kind == "hr":
                self.ln(4)


def build_pdf(md_path: Path, pdf_path: Path) -> None:
    if not FONT_REGULAR.is_file() or not FONT_BOLD.is_file():
        raise FileNotFoundError(
            "Не найден Arial Unicode / Arial Bold в /System/Library/Fonts/Supplemental/"
        )
    text = md_path.read_text(encoding="utf-8")
    blocks = parse_md(text)
    pdf = TenDushiPDF()
    pdf.render_blocks(blocks)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(pdf_path))


def main(argv: Union[List[str], None] = None) -> int:
    argv = argv or sys.argv[1:]
    md_path = Path(argv[0]) if argv else DEFAULT_MD
    pdf_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PDF
    build_pdf(md_path, pdf_path)
    size_kb = pdf_path.stat().st_size // 1024
    print(f"OK: {pdf_path} ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
