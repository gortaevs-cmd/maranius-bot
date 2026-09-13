#!/bin/bash
# Устарел для обычной работы: Maranius — prod-only на TimeWeb.
# Локальный запуск даст Conflict с контейнером (один токен, long polling).
# Оставить только для экстренной отладки: BOT_PROFILE=prod в .env и остановленный
# контейнер на сервере. Запускать из Terminal.app, не закрывать окно.

set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

pkill -f 'Python bot.py' 2>/dev/null || true
pkill -f 'python.*bot.py' 2>/dev/null || true
sleep 1

echo "Папка: $DIR"
echo "ВНИМАНИЕ: локальный bot.py конфликтует с prod на TimeWeb, если контейнер запущен."
echo "Старт bot.py (BOT_PROFILE из .env)…"
exec env PYTHONUNBUFFERED=1 .venv/bin/python bot.py
