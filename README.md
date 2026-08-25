# Maranius

Telegram-бот для ежедневных практик, ангельских знаков и VIP-материалов.

**Карточка клиента (БИЗНЕС):** [04 — Maranius](../../../04_Клиенты_и_проекты/Постоянные_клиенты/Maranius/README.md)

## Документация

Вся навигация по проекту и ссылкам: **[docs/README.md](docs/README.md)**.

Кратко: [REGISTRY.md](docs/REGISTRY.md) (команды, кнопки, env), [DATA_REGISTRY.md](docs/DATA_REGISTRY.md) (поля данных).

## Статус деплоя

**Боевой (TimeWeb):** `@MaraniusBOT` — `BOT_PROFILE=prod`, токен в `BOT_TOKEN_PROD`.
Сервер: `deploy@94.241.142.242`, путь `/opt/apps/maranius`, контейнер `maranius`.
Обновление: только цепочкой GitHub Actions «Tests → Publish verified container → Deploy verified container».
Ручной production-деплой отключён; журнал доступен администратору сервера через `docker compose logs -f maranius`.
Общий канон инфраструктуры: [CURRENT_STATE.md](../../../99_Системное/Серверы/CURRENT_STATE.md) · статус ботов: [мои_боты_статус.md](../мои_боты_статус.md).
Пользовательские JSON-данные сохраняются на сервере отдельно от образа; статический каталог карт и PDF входят в проверяемый образ.

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

На боевом сервере используется Docker. Код и статические ресурсы поставляются образом из GitHub Container Registry,
а пользовательские JSON-данные лежат в `/opt/apps/maranius/.runtime`. Telegram соединение
идёт через системный SOCKS-туннель, но сам бот больше не является systemd-службой.

После запуска найди своего бота в Telegram и напиши ему `/start` или любое сообщение.

## Проверки

Локальный запус:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

GitHub Actions автоматически запускает тесты и проверку синтаксиса при каждом `push` и pull request в `main`. Workflow также можно запустить вручную на вкладке Actions.
