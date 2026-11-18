# E1P DB Viewer

Browser worksheet for Oracle Database, including Autonomous Database. It keeps named connections, browses the schema of the signed-in database user, runs SQL and PL/SQL, and shows session and tablespace dashboards.

The service is a Python application. Java and Oracle Instant Client are not required. `python-oracledb` uses thin mode and speaks to the listener directly. Paste a JDBC URL into the connect-string form; the service translates `jdbc:oracle:thin:@...` into a driver connection. The JDK already installed on the server is not used.

## Choices for this build

- Python 3.11, 3.12, or 3.13, FastAPI, and `oracledb`
- One admin password for the web UI
- Saved connections on the server, with database passwords encrypted at rest
- Session and tablespace dashboards try DBA/`V$` views first, then fall back and show the Oracle error
- `scripts/start.sh`, `scripts/stop.sh`, `scripts/status.sh`, and a systemd unit

## Install on the server before the first run

The host needs outbound TCP to the database (usually 1521, or 1522 for Autonomous Database TCPS).

```bash
sudo dnf install -y python3.11 python3.11-pip
```

On RHEL 9, `python3` may already be 3.11 or newer. Use that if `python3 --version` reports 3.11, 3.12, or 3.13. Python 3.15 is not a target: the Oracle driver may not publish a wheel for it yet.

Also install, when you want them:

- `curl`, used by `scripts/status.sh`
- a firewall rule only if you deliberately bind a non-loopback address
- a TLS certificate if the browser is not on the same machine

Create a dedicated Linux user for production. Do not run the service as root.

```bash
sudo useradd --system --create-home --shell /sbin/nologin e1p
sudo mkdir -p /opt/e1p-db-viewer /var/lib/e1p-db-viewer/wallets
sudo rsync -a --exclude .venv --exclude data ./ /opt/e1p-db-viewer/
sudo chown -R e1p:e1p /opt/e1p-db-viewer /var/lib/e1p-db-viewer
```

Unzip an Autonomous Database wallet as that user, mode `700`:

```bash
sudo -u e1p mkdir -p /var/lib/e1p-db-viewer/wallets/prod
sudo -u e1p unzip /path/to/Wallet_DB.zip -d /var/lib/e1p-db-viewer/wallets/prod
sudo chmod 700 /var/lib/e1p-db-viewer/wallets/prod
```

Set `WALLET_ROOT=/var/lib/e1p-db-viewer/wallets` in `.env` so the service only accepts wallet directories under that folder.

## Install and start

From the application directory:

```bash
scripts/install.sh
scripts/start.sh
scripts/status.sh
```

`scripts/install.sh` creates `.venv`, installs `requirements.txt`, writes `.env`, and asks for the admin password. The password is stored as an argon2 hash. It is never written in plaintext. If that prompt was skipped, `scripts/start.sh` asks for the same password before it launches. Use at least 12 characters. A shorter password is rejected and nothing is saved.

Open `http://127.0.0.1:8080/`. The default bind address is loopback. To listen on the network, set `BIND_HOST=0.0.0.0` in `.env` and put TLS in front. `deploy/nginx.conf.example` is a starting point. When TLS terminates at a proxy, set `COOKIE_SECURE=true`.

Stop a process started by the shell script:

```bash
scripts/stop.sh
```

`scripts/start.sh` writes `data/e1p-db-viewer.pid` and appends to `data/server.log`.

### systemd

```bash
sudo chown -R e1p:e1p /opt/e1p-db-viewer
sudo -u e1p /opt/e1p-db-viewer/scripts/install.sh
cd /opt/e1p-db-viewer
sudo SERVICE_USER=e1p scripts/install-systemd.sh
```

The unit runs `.venv/bin/python -m app` in the foreground. One process only: database sessions are kept in memory.

```bash
sudo systemctl status e1p-db-viewer
sudo systemctl restart e1p-db-viewer
sudo journalctl -u e1p-db-viewer -f
```

Do not use `scripts/stop.sh` for a service that systemd is supervising. Use `systemctl stop`.

## Stop the copy on this laptop

The viewer in this checkout is started with `scripts/start.sh` and is not a boot service. The local Oracle Free database is the Podman container `oracle-free`, and its restart policy is `no`. Stopping both, and the checks that keep them from returning after a reboot, are in `documentation.md` under "Stop this laptop and keep both from starting at reboot".

## Deploy to a Google Compute Engine VM

From this laptop, with the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install-sdk) installed and signed in. On Fedora or RHEL the package is `google-cloud-cli`.

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
scripts/gce.sh deploy
scripts/gce.sh tunnel
```

`deploy` copies the application to a VM that already exists. It installs Python if needed, asks for the web UI password, and starts systemd. The password is hashed on the VM with argon2. The plaintext is not written into this repository, into `.env`, or into the systemd unit. The laptop's Oracle container, `.env`, and saved connections are not copied. The full sequence, including how to open the tunnel and how the VM service behaves across reboot, is in `documentation.md`.

The service listens on `127.0.0.1:8080` inside the VM. `scripts/gce.sh tunnel` forwards that to `http://127.0.0.1:8080/` on the laptop. Leave the tunnel command running while you use the browser. This path does not open port 8080 on the public internet, so the admin password is not sent to a world-reachable HTTP port.

If the project has more than one VM, name it:

```bash
scripts/gce.sh deploy INSTANCE --zone ZONE
scripts/gce.sh tunnel INSTANCE --zone ZONE
```

A VM without an external IP needs `--iap` on both commands. The account needs permission to open an IAP tunnel.

After the service is up, copy an Autonomous Database wallet with:

```bash
scripts/gce.sh wallet /path/to/Wallet_DB.zip --name prod
```

The directory on the VM is `/var/lib/e1p-db-viewer/wallets/prod`. Use that path in the connection form. The wallet password, if the zip has only `ewallet.p12`, is typed in the browser and stored encrypted. It is not part of the deploy command.

## Operator documents

- `documentation.md` — how to sign in, connect, browse, and run SQL
- `architecture.md` — process layout, encryption, and session handling
- `rules.md` — how further changes to this project should be made

## Rotate the admin password

```bash
.venv/bin/python -m app.admin_password
```

Restart the service afterward. For a non-interactive reset, set `ADMIN_PASSWORD` in the environment for that one command and unset it afterward. The plaintext password is not written to `.env`.

## Back up

Back up these together. The connection file is useless without the master key.

- `.env` (session secret and admin hash)
- `data/master.key`
- `data/connections.json`
- wallet directories, which live outside the app tree

If `data/master.key` is lost, edit each saved connection and enter the database password again.

## Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests do not need a running Oracle database. They cover connect-string parsing, the SQL/PL/SQL splitter, encrypted connection storage, sign-in, and the dashboard fallback when DBA views are missing.

## Packages pinned by range

Tested on 18 Nov 2026 with Python 3.12:

| Package | Role | Version seen in this build |
| --- | --- | --- |
| fastapi | HTTP API and pages | 0.164.0 |
| uvicorn | Process server | 0.54.0 |
| oracledb | Oracle thin driver (PyPI project name `oracledb`) | 26.1.0 |
| cryptography | Fernet encryption for saved passwords | 50.0.2 |
| argon2-cffi | Admin password hash | 25.1.0 |
| jinja2 | HTML templates | 3.1.6 |
| itsdangerous | Signed session cookie | installed with the runtime set |
| python-multipart | Form parser used by FastAPI | 0.0.34 |
