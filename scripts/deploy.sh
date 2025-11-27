#!/usr/bin/env bash
set -euo pipefail

# Deploy the Strava heatmap to a remote host with nginx.
# Usage:
#   ./scripts/deploy.sh --host your.server --port 8080 [--user ubuntu] [--path /opt/strava-heatmap] [--after 2024-01-01] [--start-server 1] [--enable-ssl 1] [--ssl-email you@example.com]
# Positional fallback: first arg host, second arg port.
#
# Requirements on remote: nginx, python3, ssh access; sudo permissions to write nginx config and reload.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${REMOTE_HOST:-}"
PORT="${PORT:-}"
USER="${REMOTE_USER:-ubuntu}"
REMOTE_PATH="${REMOTE_PATH:-/home/ubuntu/strava-heatmap}"
AFTER="${AFTER:-}"
START_SERVER="${START_SERVER:-1}"
SERVER_NAME="${SERVER_NAME:-fitness.sentinan.com}"
USE_SSL="${USE_SSL:-0}"
SSL_EMAIL="${SSL_EMAIL:-}"
SITE_NAME="strava-heatmap"
HTTP_PORT="${PORT:-}"
SERVICE_UNIT_PATH="/etc/systemd/system/${SITE_NAME}.service"

usage() {
  cat <<EOF
Usage: $0 [--host HOST] [--port PORT] [--user USER] [--path /remote/path] [--after 2024-01-01] [--start-server 1] [--enable-ssl 1] [--ssl-email you@example.com]
Positional fallback: first arg host, second arg port.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --user) USER="$2"; shift 2 ;;
    --path) REMOTE_PATH="$2"; shift 2 ;;
    --after) AFTER="$2"; shift 2 ;;
    --start-server) START_SERVER="$2"; shift 2 ;;
    --server-name) SERVER_NAME="$2"; shift 2 ;;
    --enable-ssl) USE_SSL="$2"; shift 2 ;;
    --ssl-email) SSL_EMAIL="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) if [[ -z "$HOST" ]]; then HOST="$1"; elif [[ -z "$HTTP_PORT" ]]; then HTTP_PORT="$1"; else echo "Unknown arg $1"; usage; exit 1; fi; shift ;;
  esac
done

# Apply defaults after positional parsing
if [[ -z "$HOST" ]]; then HOST="${REMOTE_HOST:-sentinan-dsp}"; fi
if [[ -z "$HTTP_PORT" ]]; then HTTP_PORT="${PORT:-8020}"; fi
if [[ -z "$PORT" ]]; then PORT="$HTTP_PORT"; fi

if [[ -z "$HOST" ]]; then
  echo "Set --host (or REMOTE_HOST) to deploy."
  exit 1
fi

SSH_TARGET="${USER}@${HOST}"

if [[ ! -f "$ROOT/.env" ]]; then
  echo "Warning: .env not found locally; STRAVA_* secrets will be missing on the remote." >&2
fi

echo "[deploy] Creating remote path $REMOTE_PATH on $HOST"
ssh "$SSH_TARGET" "mkdir -p \"$REMOTE_PATH\""

echo "[deploy] Syncing files to $SSH_TARGET:$REMOTE_PATH"
rsync -avz \
  --exclude ".git/" \
  --exclude ".venv/" \
  --exclude "__pycache__/" \
  --exclude "data/" \
  "$ROOT"/ \
  "$SSH_TARGET:$REMOTE_PATH/"

echo "[deploy] Installing and running on remote..."
ssh "$SSH_TARGET" bash -s <<EOF
set -euo pipefail
cd "$REMOTE_PATH"

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required on remote to serve the site (http-server). Install Node/npm and retry." >&2
  exit 1
fi
npm install

if [[ ! -f ".env" ]]; then
  echo ".env missing on remote; copy it or set STRAVA_* env vars before running." >&2
  exit 1
fi

set -a
source .env
set +a

echo "Fetching Strava activities (AFTER=${AFTER:-none})..."
python fetch_strava.py ${AFTER:+--after "$AFTER"}

if [[ "$START_SERVER" == "1" ]]; then
  # Ensure readability
  chmod -R o+rX "$REMOTE_PATH/web" "$REMOTE_PATH/data"
  find "$REMOTE_PATH/web" -type f -exec chmod 644 {} +
  find "$REMOTE_PATH/web" -type d -exec chmod 755 {} +
  find "$REMOTE_PATH/data" -type f -exec chmod 644 {} + || true
  find "$REMOTE_PATH/data" -type d -exec chmod 755 {} + || true

  cat <<SERVICE | sudo tee ${SERVICE_UNIT_PATH} >/dev/null
[Unit]
Description=Strava Heatmap static server
After=network.target

[Service]
Type=simple
WorkingDirectory=${REMOTE_PATH}
User=${USER}
Group=${USER}
Environment=PATH=${REMOTE_PATH}/node_modules/.bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/env bash -lc 'cd ${REMOTE_PATH} && PORT=${HTTP_PORT} node server.js'
Restart=always
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

  sudo systemctl daemon-reload
  sudo systemctl enable ${SITE_NAME}.service
  sudo systemctl restart ${SITE_NAME}.service
  echo "systemd service ${SITE_NAME}.service running http-server at http://127.0.0.1:${HTTP_PORT}/web/ (log: /tmp/strava_http.log)"

  # nginx reverse proxy from 80 -> ${HTTP_PORT}
  set +u
  cat <<NGINX_CONF | sudo tee /etc/nginx/sites-available/${SITE_NAME}.conf >/dev/null
server {
    listen 80;
    server_name ${SERVER_NAME} _;

    location / {
        proxy_pass http://127.0.0.1:${HTTP_PORT};
    }
}
NGINX_CONF
  set -u

  sudo ln -sf /etc/nginx/sites-available/${SITE_NAME}.conf /etc/nginx/sites-enabled/${SITE_NAME}.conf
  sudo nginx -t
  sudo systemctl reload nginx
  echo "nginx proxies http://$SERVER_NAME (port 80) -> http://127.0.0.1:${HTTP_PORT}/web/"

  if [[ "${USE_SSL}" == "1" ]]; then
    if [[ -z "${SSL_EMAIL}" ]]; then
      echo "SSL enabled but --ssl-email not provided; skipping certbot." >&2
    else
      if ! command -v certbot >/dev/null 2>&1; then
        echo "[deploy] Installing certbot..."
        sudo apt-get update
        sudo apt-get install -y certbot python3-certbot-nginx
      fi
      echo "[deploy] Requesting/renewing Let's Encrypt certificate for ${SERVER_NAME}"
      sudo certbot --nginx --non-interactive --agree-tos -m "${SSL_EMAIL}" -d "${SERVER_NAME}" --redirect
      sudo systemctl reload nginx
      echo "nginx now serves https://${SERVER_NAME} with automatic HTTP->HTTPS redirect."
    fi
  fi
fi
EOF

echo "[deploy] Deploy complete. If server started, open: http://${HOST}:${PORT}/web/"
