#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.ip.yml"
ENV_FILE="$ROOT_DIR/.env"
SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
fi

ok() { echo "[ ok ] $*"; }
warn() { echo "[warn] $*"; }
fail() { echo "[fail] $*"; }

command -v docker >/dev/null 2>&1 && ok "docker установлен" || fail "docker не установлен"
$SUDO docker compose version >/dev/null 2>&1 && ok "docker compose доступен" || fail "docker compose недоступен"

if [[ -f "$ENV_FILE" ]]; then
  ok ".env найден"
  grep -q '^BOT_TOKEN=' "$ENV_FILE" && ok "BOT_TOKEN задан" || fail "BOT_TOKEN не задан"
  grep -q '^ADMIN_IDS=' "$ENV_FILE" && ok "ADMIN_IDS задан" || fail "ADMIN_IDS не задан"
  grep -q '^PUBLIC_BASE_URL=' "$ENV_FILE" && ok "PUBLIC_BASE_URL задан" || fail "PUBLIC_BASE_URL не задан"
else
  fail ".env не найден"
fi

if [[ -f "$COMPOSE_FILE" ]]; then
  ok "docker-compose.ip.yml найден"
else
  fail "docker-compose.ip.yml не найден"
fi

if $SUDO docker ps --format '{{.Names}}' | grep -q '.'; then
  ok "есть запущенные контейнеры"
  (cd "$ROOT_DIR" && $SUDO docker compose -f "$COMPOSE_FILE" ps) || true
else
  warn "контейнеры пока не запущены"
fi
