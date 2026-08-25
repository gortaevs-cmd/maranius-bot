#!/usr/bin/env bash
# Root-only deploy entrypoint. Receives a verified GHCR image already loaded
# by the forced SSH shell and replaces the container only after health passes.
set -euo pipefail

readonly APP_DIR="/opt/apps/maranius"
readonly COMPOSE_FILE="$APP_DIR/docker-compose.production.yml"
readonly IMAGE_REPOSITORY="ghcr.io/gortaevs-cmd/maranius-bot"
readonly RUNTIME_DIR="$APP_DIR/.runtime"
readonly BACKUP_DIR="$APP_DIR/.runtime-backups"

if [[ ${SSH_ORIGINAL_COMMAND:-} != deploy\ * ]]; then
  echo "only 'deploy <40-character commit SHA>' is permitted" >&2
  exit 64
fi

readonly SHA="${SSH_ORIGINAL_COMMAND#deploy }"
if [[ ! $SHA =~ ^[0-9a-f]{40}$ ]]; then
  echo "invalid commit SHA" >&2
  exit 64
fi
readonly SHA_SHORT="$(printf %.12s "$SHA")"
readonly IMAGE="$IMAGE_REPOSITORY:$SHA"
PREVIOUS_IMAGE=""

rollback() {
  local exit_code=$?
  if [[ -n "$PREVIOUS_IMAGE" ]] && docker image inspect "$PREVIOUS_IMAGE" >/dev/null 2>&1; then
    if [[ "$PREVIOUS_IMAGE" == "$IMAGE_REPOSITORY:"* ]]; then
      previous_tag="${PREVIOUS_IMAGE#"$IMAGE_REPOSITORY:"}"
      printf 'MARANIUS_IMAGE_TAG=%s\n' "$previous_tag" > .rollback.env
      docker compose --env-file .rollback.env -f "$COMPOSE_FILE" up -d --no-build --force-recreate || true
    else
      docker compose -f "$APP_DIR/docker-compose.yml" up -d --no-build --force-recreate || true
    fi
  fi
  exit "$exit_code"
}
trap rollback ERR

cd "$APP_DIR"
test -f "$COMPOSE_FILE"
test -f .env
PREVIOUS_IMAGE=$(docker inspect --format '{{.Config.Image}}' maranius 2>/dev/null || true)
timeout 300 docker image load --quiet
docker image inspect "$IMAGE" >/dev/null

mkdir -p "$BACKUP_DIR"
backup_name="$BACKUP_DIR/pre-github-$SHA_SHORT-$(date +%Y%m%d%H%M%S).tar.gz"
tar -czf "$backup_name" users.json admins.json events.json monitored_chats.json data .runtime 2>/dev/null || \
  tar -czf "$backup_name" users.json admins.json data 2>/dev/null || true
test -s "$backup_name"

# Migrate old root-level state once. New images keep code immutable and write
# only to this directory.
if [[ ! -d "$RUNTIME_DIR" ]]; then
  install -d -m 700 -o deploy -g deploy "$RUNTIME_DIR"
  for state_file in users.json admins.json events.json monitored_chats.json; do
    if [[ -f "$state_file" ]]; then
      cp -a "$state_file" "$RUNTIME_DIR/$state_file"
    fi
  done
  install -d -m 700 -o deploy -g deploy "$RUNTIME_DIR/.json-backups"
fi

# The image contains only static data: .dockerignore excludes every mutable
# record. Copying it updates card/catalog assets without overwriting users.
stager="maranius-data-$SHA_SHORT-$$"
docker create --name "$stager" "$IMAGE" true >/dev/null
docker cp "$stager:/app/data/." "$APP_DIR/data/"
docker rm "$stager" >/dev/null

umask 077
printf 'MARANIUS_IMAGE_TAG=%s\n' "$SHA" > .deploy.env
docker compose --env-file .deploy.env -f "$COMPOSE_FILE" up -d --no-build --force-recreate

for _ in $(seq 1 18); do
  health=$(docker inspect --format '{{.State.Health.Status}}' maranius)
  if [[ "$health" == "healthy" ]]; then
    trap - ERR
    printf 'deployed %s\n' "$IMAGE"
    exit 0
  fi
  if [[ "$health" == "unhealthy" ]]; then
    echo "new container is unhealthy" >&2
    false
  fi
  sleep 5
done

echo "new container did not become healthy" >&2
false
