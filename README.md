# My Telegram Bot

Простой тестовый Telegram-бот на Python.

## Установка

1. Перейди в папку проекта:

```bash
cd "my-telegram-bot"
```

2. Создай виртуальное окружение (рекомендуется):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Установи зависимости:

```bash
pip install -r requirements.txt
```

4. Создай файл `.env` на основе примера:

```bash
cp .env.example .env
```

Открой `.env` и вставь свой токен бота от BotFather в `BOT_TOKEN=...`.

## Запуск бота

```bash
python bot.py
```

После запуска найди своего бота в Telegram и напиши ему `/start` или любое сообщение.

