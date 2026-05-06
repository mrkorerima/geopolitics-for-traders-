#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HEALTH_URL="http://127.0.0.1:8000/api/health"

stop_pid() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" >/dev/null 2>&1; then
    return 1
  fi

  kill "$pid" >/dev/null 2>&1 || return 1

  for _ in {1..10}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  return 1
}

is_radar_running() {
  local response
  response="$(curl -fsS --max-time 2 "$HEALTH_URL" 2>/dev/null || true)"
  [[ "$response" == *"gold-oil-geopolitics-radar"* ]]
}

if is_radar_running; then
  active_pid="$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  if stop_pid "$active_pid"; then
    echo "Macro Reaction Radar stopped."
    exit 0
  fi
fi

echo "No launcher-managed radar process was found."
