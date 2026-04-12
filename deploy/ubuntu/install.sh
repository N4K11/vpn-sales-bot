#!/usr/bin/env bash
        set -Eeuo pipefail

        ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
        ENV_FILE="$ROOT_DIR/.env"
        TEMPLATE_FILE="$ROOT_DIR/.env.ip.example"
        COMPOSE_FILE="$ROOT_DIR/docker-compose.ip.yml"

        if [[ ! -f "$TEMPLATE_FILE" ]]; then
          echo "[error] Не найден шаблон $TEMPLATE_FILE"
          exit 1
        fi

        if [[ ! -f "$COMPOSE_FILE" ]]; then
          echo "[error] Не найден compose-файл $COMPOSE_FILE"
          exit 1
        fi

        SUDO=""
        if [[ "${EUID}" -ne 0 ]]; then
          SUDO="sudo"
        fi
        TARGET_USER="${SUDO_USER:-$USER}"

        info() { echo "[info] $*"; }
        warn() { echo "[warn] $*"; }
        die() { echo "[error] $*"; exit 1; }

        require_cmd() {
          command -v "$1" >/dev/null 2>&1 || die "Не найдена команда: $1"
        }

        random_token() {
          if command -v openssl >/dev/null 2>&1; then
            openssl rand -hex 24
          else
            date +%s | sha256sum | cut -d' ' -f1 | cut -c1-48
          fi
        }

        upsert_env() {
          local key="$1"
          local value="$2"
          if grep -qE "^${key}=" "$ENV_FILE"; then
            sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
          else
            printf "\n%s=%s\n" "$key" "$value" >> "$ENV_FILE"
          fi
        }

        prompt_value() {
          local var_name="$1"
          local prompt_text="$2"
          local default_value="${3:-}"
          local current_value="${!var_name:-}"
          local effective_default="$current_value"
          if [[ -z "$effective_default" ]]; then
            effective_default="$default_value"
          fi
          if [[ -n "$effective_default" ]]; then
            read -r -p "$prompt_text [$effective_default]: " input || true
            printf -v "$var_name" '%s' "${input:-$effective_default}"
          else
            read -r -p "$prompt_text: " input || true
            printf -v "$var_name" '%s' "$input"
          fi
        }

        ensure_ubuntu() {
          if [[ -f /etc/os-release ]]; then
            # shellcheck disable=SC1091
            source /etc/os-release
            if [[ "${ID:-}" != "ubuntu" ]]; then
              warn "Скрипт рассчитан на Ubuntu. Обнаружено: ${PRETTY_NAME:-unknown}"
            else
              info "Обнаружена система: ${PRETTY_NAME}"
            fi
          fi
        }

        install_docker() {
          if command -v docker >/dev/null 2>&1 && $SUDO docker compose version >/dev/null 2>&1; then
            info "Docker и Compose уже установлены"
            return
          fi

          info "Устанавливаю Docker и Docker Compose plugin"
          $SUDO apt-get update
          $SUDO apt-get install -y ca-certificates curl gnupg ufw
          $SUDO install -m 0755 -d /etc/apt/keyrings
          if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
          fi

          local arch codename
          arch="$(dpkg --print-architecture)"
          codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
          echo             "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable"             | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
          $SUDO apt-get update
          $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
          $SUDO systemctl enable --now docker

          if [[ -n "${TARGET_USER}" && "${TARGET_USER}" != "root" ]]; then
            $SUDO usermod -aG docker "$TARGET_USER" || true
            warn "Пользователь $TARGET_USER добавлен в группу docker. После установки может понадобиться заново зайти в SSH-сессию."
          fi
        }

        prepare_env() {
          if [[ ! -f "$ENV_FILE" ]]; then
            cp "$TEMPLATE_FILE" "$ENV_FILE"
            info "Создан .env из .env.ip.example"
          else
            info "Использую существующий .env"
          fi

          set -a
          # shellcheck disable=SC1090
          source "$ENV_FILE"
          set +a

          prompt_value BOT_TOKEN "Введите BOT_TOKEN"
          prompt_value ADMIN_IDS "Введите ADMIN_IDS (через запятую, если несколько)"
          prompt_value BOT_USERNAME "Введите username бота без @"
          SERVER_IP_DEFAULT="${PUBLIC_BASE_URL#http://}"
          SERVER_IP_DEFAULT="${SERVER_IP_DEFAULT#https://}"
          prompt_value SERVER_IP "Введите внешний IP сервера" "$SERVER_IP_DEFAULT"
          SERVER_IP="${SERVER_IP%%:*}"
          prompt_value SUBSCRIPTION_PORT "Порт для общей ссылки и резервного кабинета" "${SUBSCRIPTION_PORT:-8080}"
          prompt_value SUPPORT_CHAT_URL "Ссылка на поддержку" "${SUPPORT_CHAT_URL:-https://t.me/your_support}"
          prompt_value CHANNEL_URL "Ссылка на канал" "${CHANNEL_URL:-https://t.me/your_channel}"
          prompt_value TERMS_URL "Ссылка на правила/пост" "${TERMS_URL:-$CHANNEL_URL}"

          if [[ -z "${WATCHTOWER_HTTP_API_TOKEN:-}" || "${WATCHTOWER_HTTP_API_TOKEN}" == "change_me_now" ]]; then
            WATCHTOWER_HTTP_API_TOKEN="$(random_token)"
          fi
          if [[ -z "${UPDATE_TRIGGER_TOKEN:-}" || "${UPDATE_TRIGGER_TOKEN}" == "change_me_now" ]]; then
            UPDATE_TRIGGER_TOKEN="$(random_token)"
          fi

          PUBLIC_BASE_URL="http://${SERVER_IP}:${SUBSCRIPTION_PORT}"
          YOOKASSA_RETURN_URL="${YOOKASSA_RETURN_URL:-${PUBLIC_BASE_URL}/payment-return}"
          UPDATE_TRIGGER_URL="http://watchtower:8080/v1/update"

          upsert_env BOT_TOKEN "$BOT_TOKEN"
          upsert_env ADMIN_IDS "$ADMIN_IDS"
          upsert_env BOT_USERNAME "$BOT_USERNAME"
          upsert_env PUBLIC_BASE_URL "$PUBLIC_BASE_URL"
          upsert_env SUBSCRIPTION_HOST "0.0.0.0"
          upsert_env SUBSCRIPTION_PORT "$SUBSCRIPTION_PORT"
          upsert_env SUPPORT_CHAT_URL "$SUPPORT_CHAT_URL"
          upsert_env CHANNEL_URL "$CHANNEL_URL"
          upsert_env TERMS_URL "$TERMS_URL"
          upsert_env UPDATE_TRIGGER_URL "$UPDATE_TRIGGER_URL"
          upsert_env UPDATE_TRIGGER_TOKEN "$UPDATE_TRIGGER_TOKEN"
          upsert_env WATCHTOWER_HTTP_API_TOKEN "$WATCHTOWER_HTTP_API_TOKEN"
          upsert_env YOOKASSA_RETURN_URL "$YOOKASSA_RETURN_URL"
          upsert_env REDIS_URL "${REDIS_URL:-redis://redis:6379/0}"
          upsert_env DATABASE_URL "${DATABASE_URL:-postgresql+asyncpg://vpn_bot:vpn_bot@postgres:5432/vpn_bot}"

          mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/logs" "$ROOT_DIR/backups"
          info "Конфиг сохранён в $ENV_FILE"
        }

        open_firewall() {
          local port="$1"
          if command -v ufw >/dev/null 2>&1; then
            $SUDO ufw allow "${port}/tcp" >/dev/null 2>&1 || true
            info "Открыл порт ${port}/tcp в ufw"
          else
            warn "ufw не установлен, порт ${port}/tcp откройте вручную"
          fi
        }

        start_stack() {
          info "Запускаю Docker-стек"
          (cd "$ROOT_DIR" && $SUDO docker compose -f "$COMPOSE_FILE" up -d --build)
        }

        print_done() {
          cat <<EOF

[done] Бот развёрнут.

Что дальше:
1. Проверь статус:      cd "$ROOT_DIR" && docker compose -f docker-compose.ip.yml ps
2. Посмотри логи бота:  cd "$ROOT_DIR" && docker compose -f docker-compose.ip.yml logs -f bot
3. Открой бота в Telegram и зайди в админку.
4. Добавь серверы 3x-ui в разделе «Серверы».
5. Проверь резервный доступ и общую ссылку по адресу:
   ${PUBLIC_BASE_URL}/access/<token>

Полезные скрипты:
- обновление: $ROOT_DIR/deploy/ubuntu/update.sh
- диагностика: $ROOT_DIR/deploy/ubuntu/doctor.sh
EOF
        }

        ensure_ubuntu
        require_cmd bash
        install_docker
        prepare_env
        open_firewall "$SUBSCRIPTION_PORT"
        start_stack
        print_done
