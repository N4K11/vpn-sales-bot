#!/usr/bin/env bash
set -euo pipefail

PORT="8799"
HOST="0.0.0.0"
TOKEN=""
TIMEOUT="25"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --token)
      TOKEN="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [[ -z "$TOKEN" ]]; then
  TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
fi

INSTALL_DIR="/opt/vpn-bot-agent"
ENV_FILE="/etc/vpn-bot-agent.env"
SERVICE_FILE="/etc/systemd/system/vpn-bot-agent.service"

sudo mkdir -p "$INSTALL_DIR"
sudo cp "$(dirname "$0")/ubuntu_agent.py" "$INSTALL_DIR/ubuntu_agent.py"
sudo cp "$(dirname "$0")/vpn-bot-agent.service" "$SERVICE_FILE"

sudo tee "$ENV_FILE" >/dev/null <<EOF
VPN_BOT_AGENT_HOST=$HOST
VPN_BOT_AGENT_PORT=$PORT
VPN_BOT_AGENT_TOKEN=$TOKEN
VPN_BOT_AGENT_TIMEOUT=$TIMEOUT
EOF

sudo chmod 600 "$ENV_FILE"
sudo chmod 755 "$INSTALL_DIR/ubuntu_agent.py"
sudo systemctl daemon-reload
sudo systemctl enable --now vpn-bot-agent.service

cat <<EOF

Ubuntu-agent установлен.

URL для бота:
http://$(hostname -I | awk '{print $1}'):$PORT

TOKEN:
$TOKEN

Не забудьте открыть порт $PORT/tcp в firewall.
Проверка:
curl -H "X-Agent-Token: $TOKEN" http://127.0.0.1:$PORT/health
EOF
