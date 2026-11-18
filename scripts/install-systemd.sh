#!/usr/bin/env bash
# Install a systemd unit for the directory this script lives in.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SERVICE_USER=${SERVICE_USER:-$(id -un)}
SERVICE_GROUP=${SERVICE_GROUP:-$(id -gn)}

if [[ "$SERVICE_USER" == "root" ]]; then
  echo "Refusing to install a unit that runs as root. Create a dedicated user and set SERVICE_USER." >&2
  exit 1
fi
if [[ ! -x "$ROOT/.venv/bin/python" || ! -f "$ROOT/.env" ]]; then
  echo "Run scripts/install.sh in $ROOT first." >&2
  exit 1
fi

UNIT=$(mktemp)
sed \
  -e "s|@INSTALL_DIR@|$ROOT|g" \
  -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
  -e "s|@SERVICE_GROUP@|$SERVICE_GROUP|g" \
  "$ROOT/deploy/e1p-db-viewer.service" > "$UNIT"

echo "Installing unit for $SERVICE_USER at $ROOT"
sudo cp "$UNIT" /etc/systemd/system/e1p-db-viewer.service
rm -f "$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now e1p-db-viewer.service
sudo systemctl --no-pager --full status e1p-db-viewer.service || true
echo "Logs: journalctl -u e1p-db-viewer -f"
