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

ask_yes_no() {
  local prompt="$1"
  local default="${2:-y}"
  local value
  read -r -p "$prompt [${default}]: " value
  value="${value:-$default}"
  case "$value" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

detect_pkg_mgr() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "apt"
    return
  fi
  if command -v dnf >/dev/null 2>&1; then
    echo "dnf"
    return
  fi
  echo "unknown"
}

install_system_packages() {
  local pkg_mgr="$1"
  case "$pkg_mgr" in
    apt)
      apt-get update
      DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
      ;;
    dnf)
      dnf install -y python3 python3-pip nginx certbot python3-certbot-nginx
      python3 -m venv --help >/dev/null 2>&1 || dnf install -y python3-virtualenv
      ;;
    *)
      echo "Unsupported package manager. Install python3, python3-venv, nginx and certbot manually."
      exit 1
      ;;
  esac
}

write_env_file() {
  local env_file="$1"
  local app_user="$2"
  local host="$3"
  local port="$4"

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

write_nginx_config() {
  local conf_file="$1"
  local domain="$2"
  local app_port="$3"

  cat > "$conf_file" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $domain;

    location / {
        proxy_pass http://127.0.0.1:$app_port;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
EOF
}

enable_nginx_site() {
  local conf_file="$1"
  if [ -d /etc/nginx/sites-enabled ] && [ -d /etc/nginx/sites-available ]; then
    ln -sf "$conf_file" "/etc/nginx/sites-enabled/skyjson.conf"
    if [ -f /etc/nginx/sites-enabled/default ]; then
      rm -f /etc/nginx/sites-enabled/default
    fi
  fi
}

main() {
  require_root

  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  APP_DIR="$(ask "Install directory" "$SCRIPT_DIR")"
  APP_USER="$(ask "Linux user that should run SkyJSON")"
  HOST="127.0.0.1"
  PORT="$(ask "Internal app port" "$DEFAULT_PORT")"

  if ! id "$APP_USER" >/dev/null 2>&1; then
    echo "User '$APP_USER' does not exist."
    exit 1
  fi

  cd "$APP_DIR"

  if [ ! -f "app.py" ]; then
    echo "app.py not found in $APP_DIR"
    exit 1
  fi

  PKG_MGR="$(detect_pkg_mgr)"
  install_system_packages "$PKG_MGR"

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
  write_env_file "$ENV_FILE" "$APP_USER" "$HOST" "$PORT"
  write_service_file "$SERVICE_FILE" "$APP_DIR" "$APP_USER" "$ENV_FILE"

  systemctl daemon-reload
  systemctl enable "${APP_NAME}.service"
  systemctl restart "${APP_NAME}.service"

  systemctl enable nginx
  systemctl start nginx

  if ask_yes_no "Configure automatic HTTPS with Let's Encrypt" "y"; then
    DOMAIN="$(ask "Public domain name for SkyJSON (must already point to this server)")"
    EMAIL="$(ask "Email address for Let's Encrypt renewal notices")"

    if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
      echo "Domain and email are required for HTTPS setup."
      exit 1
    fi

    NGINX_CONF="/etc/nginx/conf.d/skyjson.conf"
    if [ -d /etc/nginx/sites-available ]; then
      NGINX_CONF="/etc/nginx/sites-available/skyjson.conf"
    fi

    write_nginx_config "$NGINX_CONF" "$DOMAIN" "$PORT"
    enable_nginx_site "$NGINX_CONF"

    nginx -t
    systemctl reload nginx

    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect

    systemctl reload nginx

    echo
    echo "HTTPS is enabled for https://$DOMAIN"
  else
    echo
    echo "HTTPS setup skipped. SkyJSON is reachable through nginx over HTTP."
  fi

  echo
  echo "SkyJSON installation completed."
  echo "Service: ${APP_NAME}.service"
  echo "Manage app:"
  echo "  sudo systemctl status ${APP_NAME}"
  echo "  sudo systemctl restart ${APP_NAME}"
  echo "  sudo systemctl stop ${APP_NAME}"
  echo
  echo "Manage nginx:"
  echo "  sudo systemctl status nginx"
  echo "  sudo systemctl reload nginx"
}

main "$@"
