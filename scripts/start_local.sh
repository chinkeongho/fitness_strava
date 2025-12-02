#!/usr/bin/env bash
set -euo pipefail

# Start the local server, ensuring a Python venv and npm deps are installed.
# Optional env vars:
#   PORT (default 8000)
#   AFTER (e.g., 2024-01-01) to scope fetch_strava.py
#   FETCH (default 1) to run fetch_strava.py before starting

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
AFTER="${AFTER:-}"
FETCH="${FETCH:-1}"
VENV="$ROOT/.venv"
PY="$VENV/bin/python3"
PIP="$PY -m pip"

cd "$ROOT"

if [[ ! -x "$PY" ]]; then
  echo "[start] Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi

echo "[start] Activating virtualenv"
# shellcheck disable=SC1090
source "$VENV/bin/activate"

echo "[start] Ensuring pip in venv"
$PY -m ensurepip --upgrade >/dev/null
$PY -m pip install --upgrade pip

echo "[start] Installing Python requirements"
$PY -m pip install -r requirements.txt

if ! command -v npm >/dev/null 2>&1; then
  echo "[start] npm is required to serve the site. Install Node/npm and retry." >&2
  exit 1
fi

echo "[start] Installing npm dependencies"
npm install

if [[ -f ".env" ]]; then
  echo "[start] Loading .env"
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if [[ "$FETCH" == "1" ]]; then
  echo "[start] Fetching Strava activities... (AFTER=${AFTER:-none})"
  "$PY" fetch_strava.py ${AFTER:+--after "$AFTER"}
fi

echo "[start] Serving via node server.js at http://localhost:${PORT}/web/ (proxy /quote, /refresh)"
PORT="$PORT" node server.js
