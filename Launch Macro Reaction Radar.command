#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_URL_BASE="http://127.0.0.1:8000"
HEALTH_URL="http://127.0.0.1:8000/api/health"

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "Python 3 was not found on this Mac."
  exit 1
fi

APP_URL="$APP_URL_BASE/?v=$(date +%s)"

server_is_healthy() {
  local response
  response="$(curl -fsS --max-time 2 "$HEALTH_URL" 2>/dev/null || true)"
  [[ "$response" == *"gold-oil-geopolitics-radar"* ]]
}

if server_is_healthy; then
  echo "Macro Reaction Radar backend is already running."
  if ! open "$APP_URL"; then
    echo "Finder could not open the browser automatically."
    echo "Open this URL manually: $APP_URL"
  fi
  exit 0
fi

(
  for _ in {1..20}; do
    sleep 1
    if server_is_healthy; then
      open "$APP_URL" >/dev/null 2>&1 || true
      exit 0
    fi
  done

  echo ""
  echo "The dashboard did not open automatically."
  echo "Open this URL manually once the server is up: $APP_URL"
) &

echo "Starting Macro Reaction Radar..."
echo "Keep this Terminal window open while you use the dashboard."
echo "The browser will open automatically in a moment."

exec "$PYTHON_BIN" "$SCRIPT_DIR/app.py"
