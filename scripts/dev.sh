#!/usr/bin/env bash
# Run Dormammu locally: the Python service on :8000 and Next dev on :3000, from one terminal.
#
#   bash scripts/dev.sh              # real calls to the vendor roster
#   bash scripts/dev.sh --dry-run    # never dials; every row is a labeled simulated row
#   PORT=3001 bash scripts/dev.sh    # if something else holds :3000 (mdserve often does)
#
# Ctrl-C stops both. Logs: .calls/uvicorn.log (service) and the terminal (Next).
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-3000}"
API_PORT="${API_PORT:-8000}"
if [ "${1:-}" = "--dry-run" ]; then
  export DORMAMMU_DRY_RUN=1
  echo "dry run: no real calls will be placed"
fi

[ -f .env ] || { echo "no .env at repo root; copy test/.env.example and fill it in"; exit 1; }
mkdir -p .calls

if lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $API_PORT is busy; stopping the old service"
  pkill -f "uvicorn server.app:app" || true
  sleep 1
fi

uv run uvicorn server.app:app --port "$API_PORT" --reload > .calls/uvicorn.log 2>&1 &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  if curl -s -m 2 "http://127.0.0.1:$API_PORT/api/py/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done
echo "service:  http://127.0.0.1:$API_PORT/api/py/health  ->  $(curl -s -m 2 "http://127.0.0.1:$API_PORT/api/py/health" || echo 'not up yet, see .calls/uvicorn.log')"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | grep -q 'IPv4'; then
  echo "note: something else holds 127.0.0.1:$PORT; use http://localhost:$PORT (IPv6) or set PORT=3001"
fi
echo "web:      http://localhost:$PORT"
npx next dev -p "$PORT"
