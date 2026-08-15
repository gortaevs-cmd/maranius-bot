# «Тень души» — текстовый каталог

Извлечено из PDF-книги (`Карты таро - Тень души - книга.pdf`).

| Файл | Назначение |
|------|------------|
| `ИНСТРУКЦИЯ_Тень_души.pdf` | текстовая книга **100 стр.** (~4 МБ): вёрстка оригинала, без фоновых картинок |
| `ИНСТРУКЦИЯ_Тень_души.md` | редактируемая копия текстов |
| `cards_catalog.json` | машиночитаемый каталог (84 карт) |

Перегенерация PDF (компактно, как оригинал):

```bash
python3 scripts/strip_ten_dushi_to_text_pdf.py
```

Источник для PDF: `data/vip/pdf/ten_dushi_book_light.pdf` (светлая вёрстка — чёрный текст на белом; тёмная книга содержит белый текст на фоне).

Дополнительно:

```bash
python3 scripts/extract_ten_dushi_from_pdf.py   # PDF → MD + JSON
python3 scripts/md_to_pdf_ten_dushi.py          # MD → PDF (черновик, не канон)
```

Оригиналы с полной графикой: `data/vip/pdf/ten_dushi_book.pdf`, `ten_dushi_book_light.pdf`.
