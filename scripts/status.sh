#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
PID_FILE=data/e1p-db-viewer.pid

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "pid $(cat "$PID_FILE")"
else
  echo "No process recorded in $PID_FILE."
fi

PORT=8080
if [[ -f .env ]]; then
  FOUND=$(sed -n "s/^BIND_PORT=//p" .env | tail -n 1 | tr -d "'\" ")
  if [[ -n "$FOUND" ]]; then
    PORT=$FOUND
  fi
fi
if curl --silent --show-error --max-time 3 "http://127.0.0.1:${PORT}/healthz"; then
  echo
else
  echo "Health check failed on 127.0.0.1:${PORT}." >&2
  exit 1
fi
