# VIP — контент и runtime

## Файлы

| Файл / папка | Назначение |
|--------------|------------|
| `decks.json` | Тексты и структура меню колод (из SmartBot) |
| `codes.json` | Активные и отработанные коды (runtime, не в git) |
| `admin_notify.json` | Throttle уведомлений о неверных кодах |
| `pdf/` | PDF «Тень души» на сервере |

## PDF «Тень души»

Положить в `pdf/`:

- `ten_dushi_book.pdf` — основное оформление
- `ten_dushi_book_light.pdf` — светлое оформление

Источник: экспорт SmartBot (selcdn). По возможности сжать без потери качества (`qpdf --linearize` или аналог).

## Коды

Добавление: `/admin` → «➕ Добавить коды» → список одним сообщением (по строке).

Выгрузка: «📎 Выгрузка кодов» → один CSV (`code;status;user_id;username;used_at`).

## Перегенерация decks.json

Из корня `maranius/` (при обновлении SmartBot):

```bash
python3 docs/smartbot/_extract_vip_decks.py
```

(скрипт можно добавить при следующем импорте блоков)

Связи: `integrations/vip_content.py`, `integrations/vip_codes.py`, `handlers/vip.py`.

## Тексты «Тень души» (editable)

Каталог и MD-инструкция из тёмной PDF: `ten_dushi/`.

Перегенерация: `python3 scripts/extract_ten_dushi_from_pdf.py`
