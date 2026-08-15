# Smartbot — выгрузки сценариев Maranius

Сырые JSON (`/api/blocks/list`) и человекочитаемые памятки по ботам линии Maranius в SmartBot Pro.

| Бот | JSON | Памятка | Статус |
|-----|------|---------|--------|
| `@EWASMLBOT` | [EWASMLBOT_blocks.json](./EWASMLBOT_blocks.json) | [EWASMLBOT_ПАМЯТКА.md](./EWASMLBOT_ПАМЯТКА.md) | есть (2026-08-08) |
| `@KryonCrystalsBot` | [KryonCrystalsBot_blocks.json](./KryonCrystalsBot_blocks.json) | [KryonCrystalsBot_ПАМЯТКА.md](./KryonCrystalsBot_ПАМЯТКА.md) | есть (2026-08-08), 241 блок |

**Кабинет:** `karmannyj_marketolo.smartbotpro.ru`
**project_id:** `644d1628a1ec0b5089449c8e`

| Бот | `scenario_id` | Имя в кабинете |
|-----|---------------|----------------|
| EWASMLBOT | `644e3e0d349912cf2abfea20` | (bucket=dev) |
| KryonCrystalsBot | `680e1aca9818fe954024dc31` | Kryon Crystals – Меню |

## Как обновлять выгрузку

1. Открыть сценарий в кабинете (под логином).
2. DevTools → Network → обновить страницу → запрос `blocks/list`.
3. Response сохранить в соответствующий `*_blocks.json`.
4. Пересобрать памятку (или попросить Agent). Вспомогательный скрипт для Kryon из транскрипта: [`_extract_kryon.py`](./_extract_kryon.py).

## Важно

- В JSON: почты менеджеров, `table_id`, `payment_method_id`, URL Apps Script / вложений — не публиковать без нужды.
- Старая памятка EWASML (до выноса сюда): [`../SMARTBOT_SCENARIO_ПАМЯТКА.md`](../SMARTBOT_SCENARIO_ПАМЯТКА.md).
- Магазин колоды Kryon — portal в сценарий `68128eca1c49c140592e509e` («Магазин… Крайон»); отдельная выгрузка ещё не сделана.

## Связи

- Новый бот Maranius (код): [`../../`](../../)
- Меню и команды: [`../ИНТЕРФЕЙС_МЕНЮ_И_КОМАНДЫ.md`](../ИНТЕРФЕЙС_МЕНЮ_И_КОМАНДЫ.md)
