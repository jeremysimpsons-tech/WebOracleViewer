#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
PID_FILE=data/e1p-db-viewer.pid

if [[ ! -f "$PID_FILE" ]]; then
  echo "Not running (no pid file). If systemd manages it: systemctl stop e1p-db-viewer"
  exit 0
fi
PID=$(cat "$PID_FILE")
if ! kill -0 "$PID" 2>/dev/null; then
  echo "Stale pid file removed."
  rm -f "$PID_FILE"
  exit 0
fi
if [[ -r "/proc/$PID/cmdline" ]]; then
  if ! tr '\0' ' ' < "/proc/$PID/cmdline" | grep -q 'app'; then
    echo "Pid $PID is not E1P DB Viewer. Left it alone and removed the stale pid file." >&2
    rm -f "$PID_FILE"
    exit 1
  fi
fi
kill -TERM "$PID"
for _ in $(seq 1 20); do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "Stopped."
    exit 0
  fi
  sleep 0.5
done
echo "Process did not exit after SIGTERM. Sending SIGKILL." >&2
kill -KILL "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
