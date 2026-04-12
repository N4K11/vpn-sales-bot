#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.ip.yml"
SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
fi

info() { echo "[info] $*"; }

if [[ -d "$ROOT_DIR/.git" ]]; then
  info "Обновляю код из git"
  git -C "$ROOT_DIR" pull --ff-only
else
  info "Git-репозиторий не найден, пересобираю текущий код как есть"
fi

info "Пересобираю и поднимаю стек"
(cd "$ROOT_DIR" && $SUDO docker compose -f "$COMPOSE_FILE" up -d --build)

info "Готово. Статус контейнеров:"
(cd "$ROOT_DIR" && $SUDO docker compose -f "$COMPOSE_FILE" ps)
