# Using E1P DB Viewer

Sign in with the admin password chosen during `scripts/install.sh`. That password protects the web UI. It is not an Oracle password. If install finished without saving one, `scripts/start.sh` asks for it. Use at least 12 characters or the script exits without saving anything.

The service can run any SQL the saved database user can run. Keep it on a private network, or behind TLS, and keep the admin password long.

## Connections

Use **New** in the left pane. Under **How to connect**, choose one of three types. Each choice shows its own fields:

- **Classic** — host, port, and service name or SID
- **Autonomous wallet** — the wallet directory on this server and a TNS alias
- **JDBC URL or connect string** — a `jdbc:oracle:thin:@` URL, a DESCRIPTION block, or `alias?TNS_ADMIN=/path/to/wallet`

Each saved connection has **Connect** and **Disconnect**. Several can stay open at once. The green dot means that connection is open. **Working** is the one used by the worksheet, the object browser, Sessions, Tablespaces, and Runtime. **Use**, or the **Working connection** menu, changes that choice and keeps it when you switch tabs. **Disconnect** in the header closes the working connection. The others stay open.

**Test** prints the Oracle session in green when it connects, and in red when it does not. **Save** stores the profile. The password is encrypted before it is written to `data/connections.json`.

A blank password while editing means “keep the saved password.”

**Read only** blocks DML, DDL, and PL/SQL on that saved connection. `SELECT` and `WITH` still run.

### Classic

Use this for a host, port, and service name (or SID).

- TCP on port 1521 is the usual listener.
- TCPS on port 1522 is the usual Autonomous Database one-way TLS port.
- Leave **Check the database certificate name** on for TCPS.

### Autonomous wallet

1. Unzip the wallet on the server. See the README for the directory and permissions.
2. Set **Wallet directory** to that absolute path. It must sit inside `WALLET_ROOT` when that variable is set.
3. Set **TNS alias** to a name from the wallet `tnsnames.ora`, such as `mydb_high`.
4. Leave the wallet password empty when `cwallet.sso` is present (auto-login). Enter it when the directory only has `ewallet.p12`.

### JDBC URL or connect string

Paste one of these:

- `jdbc:oracle:thin:@host:1521/service`
- `jdbc:oracle:thin:@host:1521:SID`
- `jdbc:oracle:thin:@tcps://host:1522/service`
- `jdbc:oracle:thin:@(DESCRIPTION=...)`
- `jdbc:oracle:thin:@mydb_tp?TNS_ADMIN=/var/lib/e1p-db-viewer/wallets/prod`
- the same strings without the `jdbc:oracle:thin:@` prefix

**Test** opens a short Oracle session and closes it.

Switching connections while a transaction is open asks you to roll it back. The previous session is not committed for you.

## Schema browser

After a connection is open, the tree lists owners from `ALL_USERS`, then object types under the owner you expand. The signed-in database user is expanded first.

Click a table or view to see columns, constraints, indexes, optimizer statistics, and the first 100 rows. The row estimate is `NUM_ROWS` from `ALL_TABLES`. It is not a live `COUNT(*)`.

On that data view, filter with a column (contains, equals, starts with, is null) and a **WHERE** condition such as `deptno = 10 AND sal > 1000`. Do not type the word `WHERE` and do not end the condition with a semicolon. **Apply** runs the filter on the working connection. A bad filter stays on this view and names the table and the Oracle error. A good filter is shown in green with the row count.

Click a procedure, function, package, or trigger to read `ALL_SOURCE` when this user is allowed to see it.

Names that contain characters outside letters, digits, `_`, `$`, and `#` are refused instead of being pasted into SQL.

## Worksheet

Ctrl+Enter runs the selected text, or the whole script when nothing is selected.

- Separate ordinary SQL statements with `;`.
- A line that contains only `/` ends the current statement and is not sent to Oracle. Use that between PL/SQL blocks.
- `DECLARE` / `BEGIN` blocks and `CREATE PROCEDURE`, `FUNCTION`, `PACKAGE`, `TRIGGER`, and `TYPE BODY` keep the semicolons inside the block.
- `SET SERVEROUTPUT ON` and `PROMPT` are accepted. Other SQL*Plus `SET` commands stop the script with an error so they are not ignored silently.
- `SET ROLE` and `SET TRANSACTION` are real SQL and are sent to Oracle.
- Bind variables use the **Binds** box as JSON, for example `{"id": 10}`. Names are matched without case sensitivity.
- `DBMS_OUTPUT` is collected after the script when server output is on. It is on unless you send `SET SERVEROUTPUT OFF`.
- Autocommit is off until you check it. **Commit** and **Rollback** act on the pinned session.
- **Cancel** stops the statement that is running. It does not roll back. Uncommitted work stays open.
- If a later statement fails, the whole script is rolled back. Turn that off under **Settings → Database client** when you need to keep the earlier statements.
- **DROP**, **TRUNCATE**, **GRANT**, **REVOKE**, **CREATE**, **PURGE**, **FLASHBACK**, **RENAME**, and **ALTER** (except `ALTER SESSION`) ask you to confirm before they run.
- A read-only connection refuses `SELECT … FOR UPDATE` and `LOCK TABLE` when **Block locking SQL** is on. That switch is on by default. A locking statement is not kept open for **Next**.
- Uncommitted work is rolled back after 5 minutes with no activity. The database session stays open.
- A result page closes after 2 minutes with no **Next**. Run the query again to read further rows.
- Closing the browser tab disconnects the database sessions and rolls them back.
- DDL still commits inside Oracle. That is Oracle’s behavior; the worksheet does not add an extra commit around a `SELECT`.
- A `SELECT` or `WITH` query returns one page (default 50 rows, maximum 500). **Next** reads the following page from the same Oracle cursor. **Previous** shows pages already fetched in this browser. The viewer does not run `COUNT(*)` and does not load the rest of a large table. A new **Run**, a disconnect, or an idle timeout closes the open page.
- If the last page is exactly full, **Next** may return an empty page. That means the result has ended.
- A long cell is truncated for the browser.
- Each statement waits at most `DB_CALL_TIMEOUT_MS` (default 120 seconds) and then Oracle cancels it.

## Saved queries

**Library** on the worksheet stores SQL on this server, under `data/query-library.json` (mode `0600`). It is not executed until you load it and press **Run**.

- **New** adds a bucket, such as Admin queries, Business Search, or Weekly report. **Rename** and **Delete** change that bucket. Deleting a bucket removes its queries.
- Select a bucket, then **Save worksheet** to store the current worksheet text under a title.
- Click a title to put that SQL back in the worksheet. **Replace** overwrites it with the current worksheet. **Delete** removes one query.
- A bucket holds at most 100 queries, and the library holds at most 40 buckets.

## Database client safeguards

**Settings → Database client** stores these choices on the server, in `data/client-prefs.json`:

- **Block locking SQL** (on). Read-only connections cannot run `SELECT … FOR UPDATE` or `LOCK TABLE`. Locking statements read one page and close the cursor.
- **Roll back the script if a statement fails** (on). **Cancel** does not use this path.
- **Open connections** (5). Each open saved connection is one Oracle session.
- **Tag sessions** (on). `V$SESSION.ACTION` is Worksheet, Data, Sessions, Tablespaces, or Runtime. The next **Connect** also sends a keepalive every 2 minutes so Oracle can drop a client whose network died. Turn the switch off and **Connect** again to stop that keepalive.

These are always on: uncommitted work rolls back after 5 idle minutes while the session stays up, an unused result page closes after 2 minutes, and closing the browser tab disconnects and rolls back. A database session with no activity for `DB_SESSION_IDLE_SECONDS` (default 30 minutes) is still closed entirely.

The mark at the top left is the application icon. The name beside it is E1P DB Viewer.

## Sessions

The Sessions tab reads `V$SESSION` for user sessions. Summary cards count total, active, inactive, and blocked sessions. A row with `blocking_session` set is highlighted. The working connection’s own session is marked. Statement text is read from `V$SQL` only for the row you click, including during the 15-second refresh.

The grid shows SID, serial, user, status, OS user, machine, program, module, action, SQL id, wait event, blocking session, last call, and logon time. Click a row for the rest: schema, server, service, consumer group, container, terminal, port, process, client info, client identifier, the current SQL, the previous SQL, and wait details.

**Filter** matches any of those values, including SQL text that is not on the main grid. **Status** limits the list to Active, Inactive, Killed, or Blocked. Click a column heading to sort. Click it again to reverse the sort. Filter and sort stay in place while the 15-second refresh runs.

**Kill** on a row runs `ALTER SYSTEM KILL SESSION 'sid,serial#' IMMEDIATE` on the working connection. Confirm the prompt before Oracle ends that session and rolls back its open work. Killing the working connection disconnects this viewer from the database. A read-only connection disables Kill. If Oracle refuses, the message names the error. The usual grant is `ALTER SYSTEM`.

When `V$SESSION` is not granted, the page stays up and shows a banner with the Oracle error, then this connection’s own session from `SYS_CONTEXT`. That fallback has no serial number, so Kill is not offered. The banner names the grants that widen it:

```sql
GRANT SELECT ON sys.v_$session TO your_user;
GRANT SELECT ON sys.v_$sql TO your_user;
```

`SELECT_CATALOG_ROLE` covers the two views, and is broader than those grants.

## Filtering and sorting grids

Sessions, tablespaces, runtime blocks, worksheet pages, and the object browser (columns, constraints, indexes, and the fetched data page) each have a filter box and sortable headings. The filter and the sort apply to the rows already loaded. They do not scan the rest of a large table. Use the data view **WHERE** box, or an `ORDER BY` in the worksheet, when the ordering has to happen in Oracle.

## Tablespaces

The Tablespaces tab reads `DBA_TABLESPACES`, `DBA_DATA_FILES`, `DBA_FREE_SPACE`, and temporary file views. Bars mark usage. 85% and above is a warning. 95% and above is critical.

When those views are not granted, the banner shows the Oracle error and the page falls back to `USER_TABLESPACES`, `USER_SEGMENTS`, and `USER_FREE_SPACE`. Those figures are only what this user can see. They are not instance-wide. The banner names the DBA views to grant for the full dashboard.

Refresh runs on demand. The 15-second checkbox repeats the current dashboard while that tab is open.

## Runtime

The Runtime tab reads the working connection. Each block is separate, so a missing grant is named on that block and the others still load.

- Instance from `V$INSTANCE`, or this session's own names when that view is not granted
- SGA from `V$SGA` and PGA from `V$PGASTAT`
- One-minute host CPU, database CPU, average active sessions, and I/O from `V$SYSMETRIC`
- User sessions that have been active for at least 5 seconds
- Long operations still running in `V$SESSION_LONGOPS`
- The 15 statements in `V$SQL` with the most elapsed time

The banner on a block names the view and the grant that would open it. A failure that is not a missing grant names the block and the Oracle error.

## Appearance

**Settings** in the header, and the sign-in page, offer Dark, Light, Midnight, Harbor, Sand, and Contrast. The choice is stored in this browser. It does not change the server.

## Stop this laptop and keep both from starting at reboot

Two separate things are running on this Fedora laptop. Neither is installed to start at boot.

The viewer was started with `scripts/start.sh` from this project. It is not a systemd service. Oracle is the Podman container `oracle-free` (image `container-registry.oracle.com/database/free:latest-lite`, port 1521). Its restart policy is `no`. `podman.service` and `podman-restart.service` are disabled, so a reboot does not start the container.

From the project directory, stop the viewer:

```bash
cd /home/arcaneai/Coding_Projects/E1P_DB_Viewer
scripts/stop.sh
scripts/status.sh
```

`scripts/status.sh` should report that it is not running. Do not run `scripts/install-systemd.sh` on this laptop. That command would enable `e1p-db-viewer.service` and start it on the next boot.

Stop Oracle and leave the restart policy off:

```bash
podman update --restart=no oracle-free
podman stop oracle-free
podman ps -a --filter name=oracle-free
```

`podman stop` keeps the database files in the volume `oracle-data`. Do not run `podman rm` or `podman volume rm` unless you intend to discard that database. Leave these disabled:

```bash
systemctl is-enabled podman.service podman-restart.service
```

Both should print `disabled`. After a reboot, `podman ps` should not show `oracle-free` as Up, and `scripts/status.sh` should still show the viewer stopped.

To run them again later, start Oracle first, then the viewer:

```bash
podman start oracle-free
cd /home/arcaneai/Coding_Projects/E1P_DB_Viewer
scripts/start.sh
```

On this laptop the viewer listens on `http://127.0.0.1:8095/` because `BIND_PORT=8095` is set in `.env`. That port is only for this laptop.

## Start, stop, and health

```bash
scripts/start.sh
scripts/status.sh
scripts/stop.sh
```

With systemd:

```bash
sudo systemctl start e1p-db-viewer
sudo systemctl status e1p-db-viewer
sudo systemctl stop e1p-db-viewer
```

`GET /healthz` returns `{"status":"ok"}` without signing in. It does not open an Oracle session.

## Install and run on a Google Compute Engine VM

Deploy from this laptop. The script does not create a VM, and it does not install Oracle Database. The container `oracle-free` stays on the laptop. Saved connections, `.env`, and `data/` on the laptop are not uploaded. On the VM you sign in with a new web UI password and create the database connection again.

The VM copy listens on `127.0.0.1:8080` inside the VM. You open it through an SSH tunnel. The deploy does not open port 8080 on the public internet.

### Once on the laptop

Install the CLI if `gcloud` is missing. On Fedora the package is `google-cloud-cli`.

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud compute instances list
```

`instances list` must show the VM you want. If several VMs are listed, the deploy command will stop and ask you to name one.

### Deploy

From the project directory:

```bash
cd /home/arcaneai/Coding_Projects/E1P_DB_Viewer
scripts/gce.sh deploy
```

When the project has more than one VM:

```bash
scripts/gce.sh deploy INSTANCE --zone ZONE
```

A VM without an external IP needs `--iap` on deploy, tunnel, and wallet. The account needs permission to open an IAP tunnel.

```bash
scripts/gce.sh deploy INSTANCE --zone ZONE --iap
```

The first deploy asks for the web UI password twice. Use at least 12 characters. This password is only for the browser on the VM. It is hashed on the VM. The plaintext file is deleted. A later deploy keeps the hash that is already there. Change it on the VM with:

```bash
sudo -u e1p /opt/e1p-db-viewer/.venv/bin/python -m app.admin_password
sudo systemctl restart e1p-db-viewer
```

Deploy installs Python 3.12 or 3.11, creates the user `e1p`, installs the app under `/opt/e1p-db-viewer`, and enables `e1p-db-viewer.service`. A reboot of the VM starts the viewer. That is separate from the laptop, where the viewer stays stopped.

### Open it

Leave this command running:

```bash
scripts/gce.sh tunnel
```

With a named VM or IAP, repeat the same instance, zone, and `--iap` you used for deploy. Then open `http://127.0.0.1:8080/` and sign in with the password you just chose.

In the viewer, use **New** and create the Oracle connection. `127.0.0.1` in that form is the VM, not this laptop, so it will not reach the laptop container `oracle-free`.

- Classic host: the host and port the VM can reach, such as an Autonomous Database or another server.
- Wallet: from the laptop, `scripts/gce.sh wallet /path/to/Wallet_DB.zip --name prod`. The directory on the VM is `/var/lib/e1p-db-viewer/wallets/prod`. Put that path in the connection form. Add `--iap` when the VM has no external IP.

### Stop, start, and logs on the VM

```bash
gcloud compute ssh INSTANCE --zone ZONE
sudo systemctl status e1p-db-viewer
sudo systemctl stop e1p-db-viewer
sudo systemctl start e1p-db-viewer
sudo journalctl -u e1p-db-viewer -f
```

Add `--tunnel-through-iap` to `gcloud compute ssh` when the VM has no external IP. `sudo systemctl disable --now e1p-db-viewer` stops the viewer and keeps it from starting after a VM reboot. `sudo systemctl enable --now e1p-db-viewer` turns that back on.

Run `scripts/gce.sh deploy` again to update the application. The VM keeps its `.env`, admin password hash, saved connections, and wallet directories.

## If a wallet connection fails

- The directory must contain `tnsnames.ora` and either `cwallet.sso` or `ewallet.p12`.
- The service account must be able to read it. From a shell as that user, `ls` the directory.
- If a shell can read it and the systemd service cannot, check `ausearch -m avc -ts recent`. Place the wallet in a directory the service domain is allowed to read. Do not turn SELinux off to make the viewer start.
- The alias must match `tnsnames.ora` exactly.
