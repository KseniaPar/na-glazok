#!/usr/bin/env bash
# Первичная установка на Ubuntu 22.04/24.04 VDS.
# Запускать от root: bash deploy/setup.sh
set -euo pipefail

APP_DIR=/opt/na-glazok
APP_USER=naglazok
REPO_URL="${REPO_URL:-https://github.com/KseniaPar/na-glazok.git}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запусти от root: sudo bash deploy/setup.sh"
  exit 1
fi

apt-get update -y
apt-get install -y python3 python3-venv python3-pip git

id -u "$APP_USER" &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" bash -lc "
  cd '$APP_DIR'
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -e .
"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo
  echo ">>> Создан $APP_DIR/.env — впиши OPENROUTER_API_KEY и TELEGRAM_BOT_TOKEN"
  echo ">>> nano $APP_DIR/.env"
  echo
fi

install -m 644 "$APP_DIR/deploy/na-glazok.service" /etc/systemd/system/na-glazok.service
systemctl daemon-reload
systemctl enable na-glazok.service

echo
echo "Готово. После заполнения .env:"
echo "  systemctl start na-glazok"
echo "  journalctl -u na-glazok -f"
