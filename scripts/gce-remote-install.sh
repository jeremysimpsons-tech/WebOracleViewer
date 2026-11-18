#!/usr/bin/env bash
# Run on the Google Compute Engine VM. Invoked by scripts/gce.sh.
# Arguments: tarball path, optional one-time admin password file.
set -euo pipefail
umask 077

ARCHIVE=${1:-}
PASSWORD_FILE=${2:-}
INSTALL_DIR=/opt/e1p-db-viewer
STATE_DIR=/var/lib/e1p-db-viewer
SERVICE_USER=e1p
SERVICE_GROUP=e1p

wipe() {
  local target=$1
  if [[ -f "$target" ]]; then
    if command -v shred >/dev/null 2>&1; then
      shred -u "$target" || rm -f "$target"
    else
      rm -f "$target"
    fi
  fi
}

cleanup() {
  wipe "$PASSWORD_FILE"
  wipe "${STATE_DIR}/.admin-once"
  rm -f /tmp/e1p-src.tar.gz /tmp/e1p-remote-install.sh
}
trap cleanup EXIT

if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "Missing the application archive." >&2
  exit 1
fi
if ! sudo -n true 2>/dev/null; then
  echo "This VM account needs passwordless sudo, which the default Compute Engine images provide." >&2
  exit 1
fi

if command -v dnf >/dev/null 2>&1; then
  if ! sudo dnf install -y python3.12 python3.12-pip tar rsync unzip curl; then
    sudo dnf install -y python3.11 python3.11-pip tar rsync unzip curl
  fi
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip tar rsync unzip curl
else
  echo "Install Python 3.11, 3.12, or 3.13, plus tar and rsync, then run this again." >&2
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  sudo useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
sudo mkdir -p "$INSTALL_DIR" "$STATE_DIR/wallets"
sudo chmod 700 "$STATE_DIR" "$STATE_DIR/wallets"

sudo tar -C "$INSTALL_DIR" -xzf "$ARCHIVE"
sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR" "$STATE_DIR"
if command -v restorecon >/dev/null 2>&1; then
  sudo restorecon -RF "$INSTALL_DIR" "$STATE_DIR" || true
fi

if [[ -n "$PASSWORD_FILE" ]]; then
  sudo install -m 600 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$PASSWORD_FILE" "$STATE_DIR/.admin-once"
  wipe "$PASSWORD_FILE"
  PASSWORD_FILE=
fi

sudo -u "$SERVICE_USER" bash -c '
set -euo pipefail
cd /opt/e1p-db-viewer
if [[ -f /var/lib/e1p-db-viewer/.admin-once ]]; then
  ADMIN_PASSWORD=$(cat /var/lib/e1p-db-viewer/.admin-once)
  export ADMIN_PASSWORD
fi
./scripts/install.sh
unset ADMIN_PASSWORD
if [[ -f /var/lib/e1p-db-viewer/.admin-once ]]; then
  if command -v shred >/dev/null 2>&1; then
    shred -u /var/lib/e1p-db-viewer/.admin-once || rm -f /var/lib/e1p-db-viewer/.admin-once
  else
    rm -f /var/lib/e1p-db-viewer/.admin-once
  fi
fi
.venv/bin/python - <<'"'"'PY'"'"'
from pathlib import Path
from app.config import upsert_env_value
upsert_env_value(Path("/opt/e1p-db-viewer/.env"), "WALLET_ROOT", "/var/lib/e1p-db-viewer/wallets")
PY
'

cd "$INSTALL_DIR"
sudo SERVICE_USER="$SERVICE_USER" SERVICE_GROUP="$SERVICE_GROUP" ./scripts/install-systemd.sh
sudo systemctl restart e1p-db-viewer
sudo systemctl --no-pager --full status e1p-db-viewer || true
echo "The viewer is listening on 127.0.0.1:8080 inside this VM."
