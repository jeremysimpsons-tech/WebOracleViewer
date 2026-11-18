#!/usr/bin/env bash
# Deploy E1P DB Viewer to a Compute Engine VM, or open an SSH tunnel to it.
# The admin password is typed here, copied once to a mode-600 file on the VM,
# hashed with argon2, and deleted. It is not written into this repository.
set -euo pipefail
umask 077

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LOCAL_SECRET=

usage() {
  cat <<'EOF'
Deploy E1P DB Viewer to a Google Compute Engine VM from this laptop.

The service binds to 127.0.0.1 on the VM. Open it with an SSH tunnel.
This does not create a public firewall rule, and it does not store passwords.

  scripts/gce.sh deploy [INSTANCE] [--zone ZONE] [--project PROJECT] [--iap]
  scripts/gce.sh tunnel [INSTANCE] [--zone ZONE] [--project PROJECT] [--iap] [--local-port PORT]
  scripts/gce.sh wallet [INSTANCE] WALLET.zip [--name DIRECTORY] [--zone ZONE] [--project PROJECT] [--iap]

On this laptop, once:

  gcloud auth login
  gcloud config set project YOUR_PROJECT_ID

Then:

  scripts/gce.sh deploy
  scripts/gce.sh tunnel

Browse http://127.0.0.1:8080/ while the tunnel command is running.
A VM without a public IP needs --iap on both commands.
EOF
}

cleanup_local() {
  if [[ -n "$LOCAL_SECRET" && -f "$LOCAL_SECRET" ]]; then
    shred -u "$LOCAL_SECRET" 2>/dev/null || rm -f "$LOCAL_SECRET"
    LOCAL_SECRET=
  fi
}
trap cleanup_local EXIT

need_gcloud() {
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "Google Cloud CLI is not installed." >&2
    echo "Fedora or RHEL: sudo dnf install -y google-cloud-cli" >&2
    echo "Or follow https://cloud.google.com/sdk/docs/install-sdk" >&2
    exit 1
  fi
}

INSTANCE=
ZONE=
PROJECT=
IAP=0
LOCAL_PORT=8080
WALLET_NAME=prod
COMMAND=${1:-}
if [[ -z "$COMMAND" || "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then
  usage
  exit 0
fi
shift

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --zone)
      ZONE=${2:-}
      shift 2
      ;;
    --project)
      PROJECT=${2:-}
      shift 2
      ;;
    --iap)
      IAP=1
      shift
      ;;
    --local-port)
      LOCAL_PORT=${2:-}
      shift 2
      ;;
    --name)
      WALLET_NAME=${2:-}
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      POSITIONAL+=("$@")
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ ! "$LOCAL_PORT" =~ ^[0-9]+$ || "$LOCAL_PORT" -lt 1 || "$LOCAL_PORT" -gt 65535 ]]; then
  echo "The local port must be a number from 1 to 65535." >&2
  exit 1
fi
if [[ ! "$WALLET_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "The wallet directory name may contain letters, digits, dots, underscores, and hyphens." >&2
  exit 1
fi

gcloud_cmd() {
  GCLOUD=(gcloud --quiet)
  if [[ -n "$PROJECT" ]]; then
    GCLOUD+=(--project "$PROJECT")
  fi
}

resolve_instance() {
  gcloud_cmd
  local filter=()
  if [[ -n "$INSTANCE" ]]; then
    filter=(--filter "name=${INSTANCE}")
  fi
  local rows
  rows=$("${GCLOUD[@]}" compute instances list "${filter[@]}" --format='value(name,zone)')
  if [[ -z "$rows" ]]; then
    echo "No Compute Engine instance matched ${INSTANCE:-this project}." >&2
    echo "Check: gcloud auth login && gcloud config set project YOUR_PROJECT_ID" >&2
    exit 1
  fi
  local count
  count=$(printf '%s\n' "$rows" | sed '/^$/d' | wc -l)
  if [[ "$count" -ne 1 ]]; then
    echo "Name the instance and zone. More than one VM matched." >&2
    printf '%s\n' "$rows" >&2
    echo "Example: scripts/gce.sh ${COMMAND} INSTANCE --zone ZONE" >&2
    exit 1
  fi
  local line zone_field
  line=$(printf '%s\n' "$rows" | sed '/^$/d')
  INSTANCE=$(awk '{print $1}' <<<"$line")
  if [[ -z "$ZONE" ]]; then
    zone_field=$(awk '{print $2}' <<<"$line")
    ZONE=${zone_field##*/}
  fi
}

build_ssh() {
  gcloud_cmd
  SSH=("${GCLOUD[@]}" compute ssh "$INSTANCE" --zone "$ZONE")
  SCP=("${GCLOUD[@]}" compute scp --zone "$ZONE")
  if [[ "$IAP" -eq 1 ]]; then
    SSH+=(--tunnel-through-iap)
    SCP+=(--tunnel-through-iap)
  fi
}

prompt_password() {
  local first second
  read -r -s -p "Admin password for the web UI (at least 12 characters): " first >&2
  printf '\n' >&2
  read -r -s -p "Repeat admin password: " second >&2
  printf '\n' >&2
  if [[ "$first" != "$second" ]]; then
    echo "The passwords did not match." >&2
    exit 1
  fi
  if [[ ${#first} -lt 12 || "$first" == *$'\n'* ]]; then
    echo "Use at least 12 characters and do not include a line break." >&2
    exit 1
  fi
  LOCAL_SECRET=$(mktemp)
  chmod 600 "$LOCAL_SECRET"
  printf '%s' "$first" > "$LOCAL_SECRET"
  unset first second
}

pack_source() {
  local archive
  archive=$(mktemp --suffix=.tar.gz)
  tar -C "$ROOT" -czf "$archive" \
    --exclude .venv \
    --exclude data \
    --exclude .env \
    --exclude .git \
    --exclude tests \
    --exclude __pycache__ \
    --exclude .pytest_cache \
    --exclude '*.pyc' \
    .
  if tar -tzf "$archive" | grep -Eq '(^|/)\.env$|(^|/)data/'; then
    rm -f "$archive"
    echo "Refusing to upload .env or data/." >&2
    exit 1
  fi
  printf '%s\n' "$archive"
}

deploy() {
  if [[ ${#POSITIONAL[@]} -gt 1 ]]; then
    echo "deploy takes at most one instance name." >&2
    exit 1
  fi
  INSTANCE=${POSITIONAL[0]:-}
  resolve_instance
  build_ssh
  echo "Using ${INSTANCE} in ${ZONE}."

  "${SSH[@]}" --command "true"
  local remote_password=""
  if "${SSH[@]}" --command "sudo grep -q \"^ADMIN_PASSWORD_HASH='.\" /opt/e1p-db-viewer/.env"; then
    echo "This VM already has an admin password hash. It will be kept."
    echo "To change it later, on the VM: sudo -u e1p /opt/e1p-db-viewer/.venv/bin/python -m app.admin_password"
  else
    prompt_password
    local remote_home
    remote_home=$("${SSH[@]}" --command 'printf %s "$HOME"')
    remote_home=${remote_home//$'\r'/}
    remote_home=${remote_home//$'\n'/}
    remote_password="${remote_home}/e1p-admin-once"
    "${SCP[@]}" "$LOCAL_SECRET" "${INSTANCE}:${remote_password}"
    cleanup_local
    "${SSH[@]}" --command "chmod 600 $(printf '%q' "$remote_password")"
  fi

  local archive
  archive=$(pack_source)
  "${SCP[@]}" "$archive" "${INSTANCE}:/tmp/e1p-src.tar.gz"
  "${SCP[@]}" "$ROOT/scripts/gce-remote-install.sh" "${INSTANCE}:/tmp/e1p-remote-install.sh"
  rm -f "$archive"
  "${SSH[@]}" --command "chmod 700 /tmp/e1p-remote-install.sh && bash /tmp/e1p-remote-install.sh /tmp/e1p-src.tar.gz $(printf '%q' "$remote_password")"

  local project_flag="" iap_flag=""
  if [[ -n "$PROJECT" ]]; then
    project_flag=" --project ${PROJECT}"
  fi
  if [[ "$IAP" -eq 1 ]]; then
    iap_flag=" --iap"
  fi
  echo
  echo "Deployed to ${INSTANCE} in ${ZONE}."
  echo "Next: scripts/gce.sh tunnel ${INSTANCE} --zone ${ZONE}${project_flag}${iap_flag}"
  echo "Then open http://127.0.0.1:${LOCAL_PORT}/"
}

tunnel() {
  if [[ ${#POSITIONAL[@]} -gt 1 ]]; then
    echo "tunnel takes at most one instance name." >&2
    exit 1
  fi
  INSTANCE=${POSITIONAL[0]:-}
  resolve_instance
  build_ssh
  echo "Tunneling ${INSTANCE} (${ZONE}) to http://127.0.0.1:${LOCAL_PORT}/"
  echo "Leave this command running. Stop it with Ctrl+C."
  exec "${SSH[@]}" -- -N -L "${LOCAL_PORT}:127.0.0.1:8080"
}

upload_wallet() {
  local zip=""
  if [[ ${#POSITIONAL[@]} -gt 2 ]]; then
    echo "wallet takes an instance name and a zip file." >&2
    exit 1
  fi
  if [[ ${#POSITIONAL[@]} -eq 2 ]]; then
    INSTANCE=${POSITIONAL[0]}
    zip=${POSITIONAL[1]}
  elif [[ ${#POSITIONAL[@]} -eq 1 && "${POSITIONAL[0]}" == *.zip ]]; then
    zip=${POSITIONAL[0]}
  elif [[ ${#POSITIONAL[@]} -eq 1 ]]; then
    INSTANCE=${POSITIONAL[0]}
  fi
  if [[ -z "$zip" || ! -f "$zip" ]]; then
    echo "Pass the wallet zip file. Example: scripts/gce.sh wallet Wallet_DB.zip" >&2
    exit 1
  fi
  resolve_instance
  build_ssh
  "${SCP[@]}" "$zip" "${INSTANCE}:/tmp/e1p-wallet.zip"
  "${SSH[@]}" --command "sudo mkdir -p /var/lib/e1p-db-viewer/wallets/${WALLET_NAME} && sudo chown e1p:e1p /var/lib/e1p-db-viewer /var/lib/e1p-db-viewer/wallets /var/lib/e1p-db-viewer/wallets/${WALLET_NAME} && sudo -u e1p unzip -o /tmp/e1p-wallet.zip -d /var/lib/e1p-db-viewer/wallets/${WALLET_NAME} && rm -f /tmp/e1p-wallet.zip && sudo chmod 700 /var/lib/e1p-db-viewer/wallets/${WALLET_NAME} && (command -v restorecon >/dev/null && sudo restorecon -RF /var/lib/e1p-db-viewer/wallets/${WALLET_NAME} || true) && echo Wallet directory: /var/lib/e1p-db-viewer/wallets/${WALLET_NAME}"
}

need_gcloud
case "$COMMAND" in
  deploy) deploy ;;
  tunnel) tunnel ;;
  wallet) upload_wallet ;;
  *)
    echo "Unknown command: $COMMAND" >&2
    usage >&2
    exit 1
    ;;
esac
