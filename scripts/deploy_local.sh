#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
AFTER="${AFTER:-}"
VENV="$ROOT/.venv"
PY="$VENV/bin/python3"

# Optional positional port override: ./deploy_local.sh 8020
if [[ $# -ge 1 && "${1:-}" =~ ^[0-9]+$ ]]; then
  PORT="$1"
fi

cd "$ROOT"

if [[ ! -x "$PY" ]]; then
  echo "Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi

echo "Activating virtualenv"
# shellcheck disable=SC1090
source "$VENV/bin/activate"

echo "Installing requirements"
pip install -r requirements.txt
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to serve the site. Install Node/npm and retry." >&2
  exit 1
fi
echo "Installing npm dependencies"
npm install

if [[ -f ".env" ]]; then
  # Load Strava creds and optional PORT/AFTER overrides.
  set -a
  source ".env"
  set +a
fi

echo "Fetching Strava activities... (AFTER=${AFTER:-none})"
python3 fetch_strava.py ${AFTER:+--after "$AFTER"}

echo "Serving via node server.js at http://localhost:${PORT}/web/ (data from data/; proxy /quote)"
if ! PORT="$PORT" node server.js; then
  echo "server.js failed to start (port ${PORT} may be in use). Try another port: ./scripts/deploy_local.sh 8020" >&2
  exit 1
fi
