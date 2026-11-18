#!/usr/bin/env bash
# Create the virtualenv, install packages, and write .env if it is missing.
set -euo pipefail
umask 077

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

choose_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)'; then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

if ! PY=$(choose_python); then
  echo "Python 3.11, 3.12, or 3.13 is required." >&2
  echo "RHEL: sudo dnf install -y python3.11 python3.11-pip" >&2
  echo "If python3.11 is not in the default repos, enable the AppStream module or install a supported Python first." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "import oracledb, fastapi, argon2, cryptography"

if [[ ! -f .env ]]; then
  SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')
  cat > .env <<EOF
BIND_HOST=127.0.0.1
BIND_PORT=8080
SESSION_SECRET='${SECRET}'
DATA_DIR=data
MASTER_KEY_FILE=data/master.key
COOKIE_SECURE=false
SESSION_MAX_AGE_MINUTES=480
DB_CALL_TIMEOUT_MS=120000
DB_SESSION_IDLE_SECONDS=1800
MAX_FETCH_ROWS=2000
WALLET_ROOT=
AUDIT_SQL_TEXT=false
LOGIN_MAX_FAILURES=8
LOGIN_WINDOW_SECONDS=600
FORWARDED_ALLOW_IPS=127.0.0.1
SSL_CERTFILE=
SSL_KEYFILE=
LOG_LEVEL=INFO
EOF
  chmod 600 .env
  echo "Wrote .env. Review BIND_HOST, BIND_PORT, and WALLET_ROOT before exposing the service."
fi

if ! python -m app.admin_password --check; then
  echo
  echo "Choose the password you will type in the browser. Use at least 12 characters."
  echo "It is stored only as an argon2 hash. This is not the Oracle password."
  python -m app.admin_password
fi
if ! python -m app.admin_password --check; then
  echo "The web UI password was not saved in $ROOT/.env." >&2
  exit 1
fi

python - <<'PY'
from app.config import get_settings, load_env
load_env()
get_settings.cache_clear()
get_settings().ensure_storage()
print("Storage is ready.")
PY

echo "Install finished. Start the service with scripts/start.sh"
echo "For systemd, run scripts/install-systemd.sh from the directory you want the service to use."
