#!/usr/bin/env bash
# Start E1P DB Viewer. Use --foreground when systemd runs this process.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Run scripts/install.sh first." >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "Missing .env. Run scripts/install.sh." >&2
  exit 1
fi
if ! .venv/bin/python -m app.admin_password --check; then
  if [[ "${1:-}" == "--foreground" || ! -t 0 ]]; then
    echo "The web UI password is not saved in $ROOT/.env, so the service will not start." >&2
    echo "In a terminal, run: .venv/bin/python -m app.admin_password" >&2
    exit 1
  fi
  echo "The web UI password is not saved in $ROOT/.env yet."
  echo "Type it below. Use at least 12 characters. This is not the Oracle password."
  .venv/bin/python -m app.admin_password
fi

if [[ "${1:-}" == "--foreground" ]]; then
  exec .venv/bin/python -m app
fi

mkdir -p data
PID_FILE=data/e1p-db-viewer.pid
if [[ -f "$PID_FILE" ]]; then
  OLD=$(cat "$PID_FILE" || true)
  if [[ -n "${OLD}" ]] && kill -0 "$OLD" 2>/dev/null; then
    echo "Already running (pid $OLD)." >&2
    exit 1
  fi
  rm -f "$PID_FILE"
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Warning: starting as root. Use a dedicated account in production." >&2
fi

if [[ -f data/server.log ]]; then
  SIZE=$(stat -c%s data/server.log 2>/dev/null || echo 0)
  if [[ "$SIZE" -gt 20000000 ]]; then
    mv data/server.log data/server.log.1
  fi
fi

nohup .venv/bin/python -m app >> data/server.log 2>&1 &
echo $! > "$PID_FILE"
sleep 1
if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "The process exited. Check data/server.log." >&2
  exit 1
fi
echo "Started pid $(cat "$PID_FILE"). Log: data/server.log"
