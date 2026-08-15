#!/usr/bin/env bash
# Деплой maranius на Hip prod (polling + SOCKS). Запуск с Mac из папки maranius/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${MARANIUS_DEPLOY_HOST:-deploy@194.190.153.173}"
REMOTE="/opt/apps/maranius"

PY_FILES=(
  bot.py
  ui.py
  handlers/vip.py
  handlers/consent.py
  integrations/vip_codes.py
  integrations/vip_content.py
  integrations/json_storage.py
  integrations/ewasml_services.py
  integrations/platform_db.py
  integrations/user_registry.py
  integrations/inbox.py
  integrations/analytics.py
  integrations/admin_alerts.py
  events/handlers.py
  events/storage.py
  scripts/smoke_post_deploy.py
)

echo "==> 1/5 Проверка синтаксиса Python (локально)..."
cd "$ROOT"
for f in "${PY_FILES[@]}"; do
  if [[ -f "$f" ]]; then
    python3 -m py_compile "$f"
  fi
done
echo "    OK: py_compile"

echo "==> 2/5 Бэкап боевых данных до обновления..."
BACKUP_TS=$(date +%Y-%m-%d-%H%M%S)
ssh "$HOST" /bin/bash <<SSHCMD
set -euo pipefail
cd "$REMOTE"
mkdir -p .runtime-backups
runtime_files=(
  users.json
  admins.json
  events.json
  monitored_chats.json
  data/platform_users.json
  data/user_courses.json
  data/inbox.json
  data/activity_events.json
  data/activity_aggregates.json
  data/angelic/unknown_angelic.csv
  data/vip/codes.json
  data/vip/admin_notify.json
  data/vip/pdf_file_ids.json
)
existing_files=()
for f in "\${runtime_files[@]}"; do
  [[ -f "\$f" ]] && existing_files+=("\$f")
done
if (( \${#existing_files[@]} == 0 )); then
  echo "FATAL: не найдено ни одного файла боевых данных"
  exit 1
fi
tar -czf ".runtime-backups/runtime-$BACKUP_TS.tar.gz" "\${existing_files[@]}"
test -s ".runtime-backups/runtime-$BACKUP_TS.tar.gz"
echo "    backup: .runtime-backups/runtime-$BACKUP_TS.tar.gz (\${#existing_files[@]} файлов)"
SSHCMD

echo "==> 2.5/5 rsync на $HOST:$REMOTE ..."
rsync -avz --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.runtime-backups/' \
  --exclude '.json-backups/' \
  --exclude '*/.json-backups/' \
  --exclude 'users.json' \
  --exclude 'admins.json' \
  --exclude 'events.json' \
  --exclude 'monitored_chats.json' \
  --exclude 'data/platform_users.json' \
  --exclude 'data/user_courses.json' \
  --exclude 'data/inbox.json' \
  --exclude 'data/activity_events.json' \
  --exclude 'data/activity_aggregates.json' \
  --exclude 'data/angelic/unknown_angelic.csv' \
  --exclude 'data/vip/codes.json' \
  --exclude 'data/vip/admin_notify.json' \
  --exclude 'data/vip/pdf_file_ids.json' \
  --exclude '*.bak.*' \
  "$ROOT/" "$HOST:$REMOTE/"

if [[ -f "$ROOT/.env" ]]; then
  python3 - "$ROOT/.env" << 'PYENV'
import sys
from pathlib import Path
lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
out, has_proxy = [], False
for line in lines:
    if line.strip().startswith("TELEGRAM_PROXY_URL"):
        has_proxy = True
        out.append("TELEGRAM_PROXY_URL=socks5h://127.0.0.1:1080")
    else:
        out.append(line)
if not has_proxy:
    out.extend(["", "TELEGRAM_PROXY_URL=socks5h://127.0.0.1:1080"])
Path("/tmp/maranius_prod.env").write_text("\n".join(out) + "\n", encoding="utf-8")
PYENV
  scp /tmp/maranius_prod.env "$HOST:$REMOTE/.env"
  rm -f /tmp/maranius_prod.env
fi

echo "==> 3/5 Установка зависимостей и restart на сервере..."
ssh "$HOST" "set -euo pipefail
cd $REMOTE
test -d .venv || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
echo '    py_compile на сервере...'
for f in bot.py ui.py handlers/vip.py handlers/consent.py integrations/vip_codes.py integrations/vip_content.py integrations/json_storage.py integrations/ewasml_services.py integrations/user_registry.py integrations/inbox.py integrations/analytics.py integrations/admin_alerts.py scripts/smoke_post_deploy.py; do
  test -f \"\$f\" && .venv/bin/python3 -m py_compile \"\$f\"
done
sudo cp scripts/maranius-bot.service /etc/systemd/system/maranius-bot.service
sudo systemctl daemon-reload
sudo systemctl enable maranius-bot
sudo systemctl restart maranius-bot
sleep 4
STATUS=\$(systemctl is-active maranius-bot || true)
if [[ \"\$STATUS\" != \"active\" ]]; then
  echo \"FATAL: maranius-bot status=\$STATUS (ожидался active)\"
  journalctl -u maranius-bot -n 30 --no-pager || true
  exit 1
fi
if journalctl -u maranius-bot -n 8 --no-pager | grep -qE 'SyntaxError|Traceback \(most recent call last\)'; then
  echo 'FATAL: в журнале есть Traceback/SyntaxError после старта'
  journalctl -u maranius-bot -n 20 --no-pager || true
  exit 1
fi
echo \"    systemd: active\"
journalctl -u maranius-bot -n 8 --no-pager || true
"

echo "==> 4/5 Прогрев PDF file_id (если ещё нет)..."
ssh "$HOST" "set -euo pipefail
cd $REMOTE
.venv/bin/python3 scripts/warm_pdf_file_ids.py || echo 'WARN warm PDF skipped'
"

echo "==> 5/5 Smoke: getMe через Telegram API..."
ssh "$HOST" "set -euo pipefail
cd $REMOTE
.venv/bin/python3 scripts/smoke_post_deploy.py
"

echo ""
echo "DEPLOY OK: maranius-bot active, smoke passed."
