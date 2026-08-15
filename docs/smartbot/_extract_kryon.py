#!/usr/bin/env python3
"""One-shot: extract Kryon dump from agent transcript → JSON + memo."""
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

TRANSCRIPT = Path(
    "/Users/sergeygortaev/.cursor/projects/"
    "Users-sergeygortaev-Library-CloudStorage-GoogleDrive-gortaev-s-gmail-com-CursorG/"
    "agent-transcripts/3e1c2634-013f-4407-baf1-71a03cd78db4/"
    "3e1c2634-013f-4407-baf1-71a03cd78db4.jsonl"
)
OUT_DIR = Path(__file__).resolve().parent
OUT_JSON = OUT_DIR / "KryonCrystalsBot_blocks.json"
OUT_MEMO = OUT_DIR / "KryonCrystalsBot_ПАМЯТКА.md"
SCENARIO_ID = "680e1aca9818fe954024dc31"


def load_dump() -> dict:
    raw_text = None
    for line in TRANSCRIPT.open():
        if SCENARIO_ID not in line or len(line) < 50000:
            continue
        obj = json.loads(line)
        for c in obj.get("message", {}).get("content", []):
            if c.get("type") != "text":
                continue
            t = c.get("text", "")
            if SCENARIO_ID in t and "blocks" in t:
                raw_text = t
                break
        if raw_text:
            break
    if not raw_text:
        raise SystemExit("Kryon JSON not found in transcript")
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)
    data = json.loads(raw_text)
    assert data.get("status") == "ok"
    blocks = data["data"]["blocks"]
    assert isinstance(blocks, list) and blocks
    assert all(b.get("scenario_id") == SCENARIO_ID for b in blocks)
    return data


def write_memo(data: dict) -> None:
    blocks = data["data"]["blocks"]
    kinds = Counter(f"{b.get('type')}/{b.get('kind')}" for b in blocks)
    named = [(b.get("name") or "").strip() for b in blocks if (b.get("name") or "").strip()]

    commands = []
    for b in blocks:
        if b.get("kind") != "chat_message":
            continue
        for cond in (b.get("params") or {}).get("message_conditions") or []:
            if cond.get("key") == "%message_text%" and str(cond.get("value", "")).startswith("/"):
                commands.append((cond["value"], (b.get("name") or "").strip(), b["_id"]))

    http_blocks = []
    for b in blocks:
        if b.get("kind") != "http_request":
            continue
        p = b.get("params") or {}
        http_blocks.append((b.get("name") or "(без имени)", p.get("method"), p.get("url"), b["_id"]))

    portals = []
    for b in blocks:
        if b.get("kind") != "portal_out":
            continue
        ti = (b.get("extra_info") or {}).get("target") or {}
        sc = ti.get("scenario") or {}
        portals.append((b.get("name") or "", sc.get("name"), sc.get("_id"), b["_id"]))

    payment_buttons = []
    broken_buttons = []
    for b in blocks:
        kb = (b.get("params") or {}).get("keyboard") or {}
        for row in kb.get("buttons") or []:
            for btn in row:
                text = btn.get("text") or ""
                kind = btn.get("kind")
                if kind == "create_payment":
                    payment_buttons.append(
                        (text, btn.get("payment_amount"), btn.get("payment_description"), b.get("name") or "", b["_id"])
                    )
                if kind == "transition" and not btn.get("block_id"):
                    broken_buttons.append((text, b.get("name") or "", b["_id"]))
                if kind == "open_url" and not (btn.get("url") or "").strip():
                    broken_buttons.append((f"{text} (пустой url)", b.get("name") or "", b["_id"]))

    vars_seen = set()
    for b in blocks:
        blob = json.dumps(b.get("params") or {}, ensure_ascii=False)
        for m in re.findall(r"%[A-Za-zА-Яа-я0-9_]+%", blob):
            vars_seen.add(m)

    lists = []
    for b in blocks:
        if b.get("kind") == "add_to_list":
            lists.append(((b.get("params") or {}).get("list_id"), b.get("name") or "", b["_id"]))

    sheets = []
    for b in blocks:
        if b.get("kind") == "add_row_google_sheets":
            p = b.get("params") or {}
            sheets.append((p.get("table_name"), p.get("sheet_name"), b.get("name") or "", b["_id"]))

    timers = sum(1 for b in blocks if b.get("kind") == "set_timer")
    send_msg = sum(1 for b in blocks if b.get("kind") == "send_message")
    today = date.today().isoformat()

    lines = [
        "# KryonCrystalsBot — структура сценария SmartBot Pro",
        "",
        "> Канон для бота **@KryonCrystalsBot** (сценарий «Kryon Crystals – Меню»).",
        "> Сырой JSON: [`KryonCrystalsBot_blocks.json`](KryonCrystalsBot_blocks.json).",
        "> Как выгружать снова: [`README.md`](README.md) в этой папке.",
        "",
        f"**Дата выгрузки:** {today}  ",
        "**Источник:** кабинет → DevTools → `/api/blocks/list`  ",
        "**Cabinet:** `karmannyj_marketolo.smartbotpro.ru`  ",
        "**project_id:** `644d1628a1ec0b5089449c8e`  ",
        f"**scenario_id:** `{SCENARIO_ID}`  ",
        "**bucket:** `dev`  ",
        f"**Блоков:** {len(blocks)}",
        "",
        "---",
        "",
        "## Что это за бот",
        "",
        "Основной продуктный сценарий **«Световой путь»** (кристаллы Крайона / Мараниус): "
        "маршруты **7 / 21 / 33 дня**, короткие маршруты по жизненным задачам (Простить, Отпустить и др.), "
        "оплаты через ЮKassa, ежедневные послания по таймерам (~19:00), коды наклеек 3D, магазин колоды, политика ПДн.",
        "",
        "---",
        "",
        "## Команды меню (`/…`)",
        "",
    ]

    if commands:
        lines += ["| Команда | Имя блока |", "|---------|-----------|"]
        for cmd, name, _bid in sorted(set(commands), key=lambda x: x[0]):
            lines.append(f"| `{cmd}` | {name or '—'} |")
    else:
        lines.append("_Глобальных `/команд` в дампе не найдено._")

    lines += [
        "",
        "---",
        "",
        "## Типы блоков",
        "",
        "| type/kind | Кол-во |",
        "|-----------|--------|",
    ]
    for k, n in kinds.most_common():
        lines.append(f"| `{k}` | {n} |")
    lines += [
        "",
        f"- Сообщений (`send_message`): **{send_msg}**",
        f"- Таймеров (`set_timer`): **{timers}** — цепочки дней маршрутов",
        "",
        "---",
        "",
        "## Оплаты (кнопки ЮKassa)",
        "",
    ]

    if payment_buttons:
        lines += [
            "| Текст кнопки | Сумма / переменная | Описание | Откуда |",
            "|--------------|--------------------|----------|--------|",
        ]
        seen = set()
        for text, amount, desc, bname, _bid in payment_buttons:
            key = (text, amount, desc)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"| {text} | `{amount}` | {desc or '—'} | {bname or '—'} |")
    else:
        lines.append("_Нет._")

    lines += ["", "---", "", "## HTTP-запросы", ""]
    if http_blocks:
        for name, method, url, _bid in http_blocks:
            lines.append(f"- **{name}** — `{method}` `{url}`")
    else:
        lines.append("_Нет._")

    lines += ["", "---", "", "## Порталы в другие сценарии", ""]
    if portals:
        for name, sc_name, sc_id, _bid in portals:
            lines.append(f"- **{name or 'portal'}** → `{sc_name}` (`{sc_id}`)")
    else:
        lines.append("_Нет._")

    lines += ["", "---", "", "## Google Sheets", ""]
    if sheets:
        for tname, sname, bname, _bid in sheets:
            lines.append(f"- {bname or '—'} → таблица «{tname}», лист «{sname}»")
    else:
        lines.append("_Нет._")

    lines += ["", "---", "", "## Списки пользователей (`add_to_list`)", ""]
    for lid, name, _bid in lists:
        lines.append(f"- `{lid}` — {name or 'без имени'}")

    lines += ["", "---", "", "## Переменные (по дампу)", ""]
    sorted_vars = sorted(vars_seen)
    lines.append(", ".join(f"`{v}`" for v in sorted_vars[:80]))
    if len(sorted_vars) > 80:
        lines.append(f"\n_… и ещё {len(sorted_vars) - 80}_")

    lines += ["", "---", "", "## Битые / пустые кнопки (проверить в кабинете)", ""]
    if broken_buttons:
        for text, bname, bid in broken_buttons:
            lines.append(f"- «{text}» в блоке «{bname or '—'}» (`{bid[:8]}…`)")
    else:
        lines.append("_Не найдено по эвристике `transition` без `block_id` / пустой `url`._")

    lines += ["", "---", "", "## Именованные блоки (основные, не дни)", ""]
    skip_re = re.compile(r"ДЕНЬ\s+\d+|День\s+\d+|Маршрут на \d", re.I)
    main_names = []
    for n in named:
        if skip_re.search(n):
            continue
        if n not in main_names:
            main_names.append(n)
    for n in main_names[:60]:
        lines.append(f"- {n}")
    if len(main_names) > 60:
        lines.append(f"- _… ещё {len(main_names) - 60}_")

    lines += [
        "",
        "---",
        "",
        "## Карта продукта (сжато)",
        "",
        "1. **Старт** → приветствие → проверка `%Email%` → меню практик / путь через туман.",
        "2. **Путь через Туман:** выбор 7 / 21 / 33 (и пакет) → оплата → метка статистики → таймеры дней → финалы с апселлом.",
        "3. **Маршруты под задачу:** Простить / Отпустить (и заготовки других) — 1 / 3 / 7 дней.",
        "4. **Наклейки 3D:** коды `*888` → switch → текст кристалла.",
        "5. **Магазин колоды:** `/buy_deck` → portal в сценарий магазина.",
        "6. **Fallback:** default_answer → notify → Google Sheets «сценарий не подошёл».",
        "",
        "---",
        "",
        "## Связанные файлы проекта",
        "",
        "- [`../ИНТЕРФЕЙС_МЕНЮ_И_КОМАНДЫ.md`](../ИНТЕРФЕЙС_МЕНЮ_И_КОМАНДЫ.md)",
        "- [`EWASMLBOT_ПАМЯТКА.md`](EWASMLBOT_ПАМЯТКА.md) — основной бот экосистемы",
        "- [`README.md`](README.md) — каталог выгрузок",
        "",
    ]
    OUT_MEMO.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"Wrote {OUT_MEMO.name}: commands={len(commands)} "
        f"payments={len(payment_buttons)} broken={len(broken_buttons)} timers={timers}"
    )


def main() -> None:
    data = load_dump()
    blocks = data["data"]["blocks"]
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_JSON.name}: blocks={len(blocks)} bytes={OUT_JSON.stat().st_size}")
    write_memo(data)


if __name__ == "__main__":
    main()
