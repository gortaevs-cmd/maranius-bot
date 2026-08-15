#!/usr/bin/env python3
"""Убрать фон/картинки из PDF «Тень души», оставить текстовый слой (100 стр.)."""

from __future__ import annotations

import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "data" / "vip" / "pdf" / "ten_dushi_book_light.pdf"
DEFAULT_OUT = ROOT / "data" / "vip" / "ten_dushi" / "ИНСТРУКЦИЯ_Тень_души.pdf"


def strip_to_text_pdf(src: Path, out: Path) -> dict[str, int]:
    """Удалить растровые слои; текст и вёрстка страниц сохраняются."""
    if not src.is_file():
        raise FileNotFoundError(f"Нет исходного PDF: {src}")

    doc = fitz.open(src)
    removed = 0
    for page in doc:
        for img in page.get_images(full=True):
            try:
                page.delete_image(img[0])
                removed += 1
            except Exception:
                pass
        page.clean_contents()

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out), garbage=4, deflate=True)
    doc.close()

    out_doc = fitz.open(out)
    stats = {
        "pages": out_doc.page_count,
        "chars": sum(len(out_doc[i].get_text()) for i in range(out_doc.page_count)),
        "images_removed": removed,
        "size_kb": out.stat().st_size // 1024,
    }
    out_doc.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    src = Path(argv[0]) if argv else DEFAULT_SRC
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    stats = strip_to_text_pdf(src, out)
    print(
        f"OK: {out} — {stats['pages']} стр., {stats['size_kb']} KB, "
        f"символов {stats['chars']}, удалено image-xref: {stats['images_removed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
