# Maranius

Telegram-бот для ежедневных практик, ангельских знаков и VIP-материалов.

**Карточка клиента (БИЗНЕС):** [04 — Maranius](../../../04_Клиенты_и_проекты/Постоянные_клиенты/Maranius/README.md)

## Документация

Вся навигация по проекту и ссылкам: **[docs/README.md](docs/README.md)**.

Кратко: [REGISTRY.md](docs/REGISTRY.md) (команды, кнопки, env), [DATA_REGISTRY.md](docs/DATA_REGISTRY.md) (поля данных).

## Статус деплоя

**Боевой (Hip prod):** `@MaraniusBOT` — `BOT_PROFILE=prod`, токен в `BOT_TOKEN_PROD`.
Сервер: `deploy@194.190.153.173`, путь `/opt/apps/maranius`, systemd `maranius-bot`. Деплой: `./scripts/deploy_hip_prod.sh`.

**Стенд (Mac):** `@angelic_signs_bot` — `BOT_PROFILE=test`, токен в `BOT_TOKEN_TEST`. Песочница `/me` `/rate`: [`../Bot_TEST_EL_bot/`](../Bot_TEST_EL_bot/).

## Установка

1. Перейди в папку проекта:

```bash
cd "maranius"
```

2. Создай виртуальное окружение (рекомендуется):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Если папку проекта **переносили** или копировали с другого пути, старый `.venv` перестаёт работать (внутри зашит абсолютный путь к Python). Удали `.venv` и создай заново командами выше, затем снова `pip install -r requirements.txt`.

3. Установи зависимости:

```bash
pip install -r requirements.txt
```

4. Создай файл `.env` на основе примера:

```bash
cp .env.example .env
```

Открой `.env`:

- **Два бота в одном файле:** задай `BOT_PROFILE=test` или `prod`, плюс `BOT_TOKEN_TEST` и `BOT_TOKEN_PROD`. В режиме **test** используется **только** `BOT_TOKEN_TEST` (без подстановки `BOT_TOKEN`, чтобы случайно не взять прод). Для **prod** при пустом `BOT_TOKEN_PROD` подставится `BOT_TOKEN`.
- **По-старому:** одна строка `BOT_TOKEN=...` и без `BOT_PROFILE` — считается прод, как раньше.
- Значения из `.env` **перекрывают** переменные, заданные в терминале (`export ...`), чтобы не путаться с профилем.

## Запуск бота

```bash
python3 bot.py
```

(После `source .venv/bin/activate` подойдёт и `python bot.py`, если он указывает на venv.)

После запуска найди своего бота в Telegram и напиши ему `/start` или любое сообщение.
