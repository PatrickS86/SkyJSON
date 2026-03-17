#!/usr/bin/env bash
set -euo pipefail

APP_NAME="skyjson"
DEFAULT_PORT="8000"

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Please run this installer with sudo or as root."
    exit 1
  fi
}

ask() {
  local prompt="$1"
  local default="${2:-}"
  local value
  if [ -n "$default" ]; then
    read -r -p "$prompt [$default]: " value
    echo "${value:-$default}"
  else
    read -r -p "$prompt: " value
    echo "$value"
  fi
}

write_env_file() {
  local env_file="$1"
  local app_dir="$2"
  local app_user="$3"
  local host="$4"
  local port="$5"

  cat > "$env_file" <<EOF
SKYJSON_HOST=$host
SKYJSON_PORT=$port
SKYJSON_DEBUG=0
PYTHONUNBUFFERED=1
EOF

  chown "$app_user:$app_user" "$env_file"
  chmod 640 "$env_file"
}

write_service_file() {
  local service_file="$1"
  local app_dir="$2"
  local app_user="$3"
  local env_file="$4"

  cat > "$service_file" <<EOF
[Unit]
Description=SkyJSON web application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$app_user
Group=$app_user
WorkingDirectory=$app_dir
EnvironmentFile=$env_file
ExecStart=$app_dir/.venv/bin/python $app_dir/app.py
Restart=always
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
}

main() {
  require_root

  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  APP_DIR="$(ask "Install directory" "$SCRIPT_DIR")"
  APP_USER="$(ask "Linux user that should run SkyJSON")"
  HOST="$(ask "Bind host" "0.0.0.0")"
  PORT="$(ask "Bind port" "$DEFAULT_PORT")"

  if ! id "$APP_USER" >/dev/null 2>&1; then
    echo "User '$APP_USER' does not exist."
    exit 1
  fi

  cd "$APP_DIR"

  if [ ! -f "app.py" ]; then
    echo "app.py not found in $APP_DIR"
    exit 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but is not installed."
    exit 1
  fi

  if [ ! -d ".venv" ]; then
    python3 -m venv .venv
  fi

  ./.venv/bin/pip install --upgrade pip
  if [ -f "requirements.txt" ]; then
    ./.venv/bin/pip install -r requirements.txt
  else
    ./.venv/bin/pip install flask requests werkzeug
  fi

  chown -R "$APP_USER:$APP_USER" "$APP_DIR"

  ENV_FILE="/etc/default/${APP_NAME}"
  SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

  write_env_file "$ENV_FILE" "$APP_DIR" "$APP_USER" "$HOST" "$PORT"
  write_service_file "$SERVICE_FILE" "$APP_DIR" "$APP_USER" "$ENV_FILE"

  systemctl daemon-reload
  systemctl enable "${APP_NAME}.service"
  systemctl restart "${APP_NAME}.service"

  echo
  echo "SkyJSON installation completed."
  echo "Service: ${APP_NAME}.service"
  echo "Commands:"
  echo "  sudo systemctl status ${APP_NAME}"
  echo "  sudo systemctl restart ${APP_NAME}"
  echo "  sudo systemctl stop ${APP_NAME}"
  echo
  echo "The service is enabled and will start automatically when the server boots."
}

main "$@"
