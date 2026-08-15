#!/bin/bash
# Запуск бота на Mac (long polling). Какой бот — BOT_PROFILE в .env (test=@angelic_signs_bot, prod=@MaraniusBOT).
# Запускать из Terminal.app, не закрывать окно.

set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

pkill -f 'Python bot.py' 2>/dev/null || true
pkill -f 'python.*bot.py' 2>/dev/null || true
sleep 1

echo "Папка: $DIR"
echo "Старт bot.py (BOT_PROFILE из .env)…"
exec env PYTHONUNBUFFERED=1 .venv/bin/python bot.py
