"""Session and tablespace dashboards.

DBA and V$ views are tried first. When they are missing, the report falls back
and keeps the Oracle error in ``notice`` so the operator can see why the
instance-wide numbers are not there.
"""

from __future__ import annotations

from collections.abc import Callable

from app.errors import AppError, QueryFailed, first_line, is_missing_privilege

Fetcher = Callable[[str], list[dict]]

_SESSIONS = """
SELECT s.sid AS "sid",
       s.serial# AS "serial",
       s.username AS "username",
       s.status AS "status",
       s.schemaname AS "schema_name",
       s.osuser AS "osuser",
       s.machine AS "machine",
       s.terminal AS "terminal",
       s.program AS "program",
       s.module AS "module",
       s.action AS "action",
       s.client_info AS "client_info",
       s.client_identifier AS "client_identifier",
       s.process AS "process",
       s.port AS "port",
       s.type AS "session_type",
       s.server AS "server",
       s.service_name AS "service_name",
       s.resource_consumer_group AS "consumer_group",
       s.con_id AS "con_id",
       TO_CHAR(s.logon_time, 'YYYY-MM-DD HH24:MI:SS') AS "logon_time",
       s.last_call_et AS "last_call_et",
       s.sql_id AS "sql_id",
       s.sql_child_number AS "sql_child_number",
       TO_CHAR(s.sql_exec_start, 'YYYY-MM-DD HH24:MI:SS') AS "sql_exec_start",
       s.prev_sql_id AS "prev_sql_id",
       s.prev_child_number AS "prev_child_number",
       TO_CHAR(s.prev_exec_start, 'YYYY-MM-DD HH24:MI:SS') AS "prev_exec_start",
       s.event AS "event",
       s.wait_class AS "wait_class",
       s.state AS "state",
       s.seconds_in_wait AS "seconds_in_wait",
       s.blocking_session AS "blocking_session",
       s.blocking_session_status AS "blocking_session_status",
       s.final_blocking_session AS "final_blocking_session"
FROM v$session s
WHERE s.type = 'USER'
ORDER BY s.status, s.username, s.sid
"""

_SESSIONS_SHORT = """
SELECT sid AS "sid",
       serial# AS "serial",
       username AS "username",
       status AS "status",
       osuser AS "osuser",
       machine AS "machine",
       program AS "program",
       TO_CHAR(logon_time, 'YYYY-MM-DD HH24:MI:SS') AS "logon_time",
       last_call_et AS "last_call_et",
       event AS "event",
       blocking_session AS "blocking_session"
FROM v$session
WHERE type = 'USER'
ORDER BY status, username, sid
"""

_SESSION_CONTEXT = """
SELECT TO_NUMBER(SYS_CONTEXT('USERENV','SID')) AS "sid",
       SYS_CONTEXT('USERENV','SESSION_USER') AS "username",
       'ACTIVE' AS "status",
       SYS_CONTEXT('USERENV','HOST') AS "machine",
       SYS_CONTEXT('USERENV','SERVICE_NAME') AS "service_name",
       SYS_CONTEXT('USERENV','DB_NAME') AS "db_name",
       SYS_CONTEXT('USERENV','INSTANCE_NAME') AS "instance_name",
       SYS_CONTEXT('USERENV','SERVER_HOST') AS "server_host"
FROM dual
"""

_PERM_TABLESPACES = """
SELECT ts.tablespace_name AS "tablespace_name",
       ts.contents AS "contents",
       ts.status AS "status",
       ROUND(df.bytes / 1024 / 1024, 1) AS "total_mb",
       ROUND((df.bytes - NVL(fs.bytes, 0)) / 1024 / 1024, 1) AS "used_mb",
       ROUND(NVL(fs.bytes, 0) / 1024 / 1024, 1) AS "free_mb",
       ROUND((df.bytes - NVL(fs.bytes, 0)) / NULLIF(df.bytes, 0) * 100, 1) AS "pct_used",
       ROUND(df.max_bytes / 1024 / 1024, 1) AS "max_mb",
       df.files AS "files"
FROM dba_tablespaces ts
JOIN (
    SELECT tablespace_name,
           SUM(bytes) AS bytes,
           SUM(CASE WHEN maxbytes = 0 THEN bytes ELSE maxbytes END) AS max_bytes,
           COUNT(*) AS files
    FROM dba_data_files
    GROUP BY tablespace_name
) df ON df.tablespace_name = ts.tablespace_name
LEFT JOIN (
    SELECT tablespace_name, SUM(bytes) AS bytes
    FROM dba_free_space
    GROUP BY tablespace_name
) fs ON fs.tablespace_name = ts.tablespace_name
WHERE ts.contents <> 'TEMPORARY'
ORDER BY ts.tablespace_name
"""

_TEMP_TABLESPACES = """
SELECT ts.tablespace_name AS "tablespace_name",
       'TEMPORARY' AS "contents",
       ts.status AS "status",
       ROUND(tf.bytes / 1024 / 1024, 1) AS "total_mb",
       ROUND((tf.bytes - NVL(fr.bytes, 0)) / 1024 / 1024, 1) AS "used_mb",
       ROUND(NVL(fr.bytes, 0) / 1024 / 1024, 1) AS "free_mb",
       ROUND((tf.bytes - NVL(fr.bytes, 0)) / NULLIF(tf.bytes, 0) * 100, 1) AS "pct_used",
       ROUND(tf.max_bytes / 1024 / 1024, 1) AS "max_mb",
       tf.files AS "files"
FROM dba_tablespaces ts
JOIN (
    SELECT tablespace_name,
           SUM(bytes) AS bytes,
           SUM(CASE WHEN maxbytes = 0 THEN bytes ELSE maxbytes END) AS max_bytes,
           COUNT(*) AS files
    FROM dba_temp_files
    GROUP BY tablespace_name
) tf ON tf.tablespace_name = ts.tablespace_name
LEFT JOIN (
    SELECT tablespace_name, SUM(free_space) AS bytes
    FROM dba_temp_free_space
    GROUP BY tablespace_name
) fr ON fr.tablespace_name = ts.tablespace_name
ORDER BY ts.tablespace_name
"""


def build_sessions(fetch: Fetcher) -> dict:
    notice = None
    source = "v$session"
    rows = None
    errors: list[str] = []
    for sql, kind in (
        (_SESSIONS, "full"),
        (_SESSIONS_SHORT, "short"),
    ):
        try:
            rows = fetch(sql)
        except QueryFailed as exc:
            if not is_missing_privilege(exc.message):
                raise
            errors.append(first_line(exc.message))
            rows = None
            continue
        if kind == "short":
            notice = (
                "Showing a shorter session list (" + " ".join(errors) + ")."
            )
        break
    if rows is None:
        rows = fetch(_SESSION_CONTEXT)
        source = "session_context"
        notice = (
            f"Instance session views are not available ({' '.join(errors)}). "
            "Showing this connection's own session only. "
            "To see every session, grant SELECT on V_$SESSION (the view behind the V$SESSION synonym), "
            "or grant SELECT_CATALOG_ROLE. "
            "Grant SELECT on V_$SQL to see the statement text. "
            "ALTER SYSTEM is required to kill a session."
        )
    current_sid = None
    current_serial = None
    if source == "v$session":
        current_sid = _current_sid(fetch)
        for row in rows:
            if current_sid is not None and str(row.get("sid")) == str(current_sid):
                current_serial = row.get("serial")
                break
    return {
        "ok": True,
        "source": source,
        "notice": notice,
        "rows": rows,
        "summary": _session_summary(rows),
        "current_sid": current_sid,
        "current_serial": current_serial,
    }


def build_session_sql(fetch_binds, sid: int, serial: int) -> dict:
    """Read the current and previous SQL for one session. The list view does not."""
    binds = {"sid": sid, "serial": serial}
    try:
        rows = fetch_binds(_SESSION_SQL, binds)
    except QueryFailed as exc:
        if not is_missing_privilege(exc.message):
            raise
        try:
            rows = fetch_binds(_SESSION_SQL_IDS, binds)
        except QueryFailed as second:
            raise second
        return {
            "found": bool(rows),
            "notice": (
                "Statement text was not read (" + first_line(exc.message) + "). "
                "Grant SELECT on V_$SQL. The SQL id from V$SESSION is shown when it is available."
            ),
            **(rows[0] if rows else {}),
        }
    if not rows:
        return {"found": False, "notice": "That session is no longer in V$SESSION."}
    return {"found": True, "notice": None, **rows[0]}


_SESSION_SQL = """
SELECT s.sql_id AS "sql_id",
       s.sql_child_number AS "sql_child_number",
       TO_CHAR(s.sql_exec_start, 'YYYY-MM-DD HH24:MI:SS') AS "sql_exec_start",
       s.prev_sql_id AS "prev_sql_id",
       s.prev_child_number AS "prev_child_number",
       TO_CHAR(s.prev_exec_start, 'YYYY-MM-DD HH24:MI:SS') AS "prev_exec_start",
       (
           SELECT SUBSTR(q.sql_text, 1, 4000)
           FROM v$sql q
           WHERE q.sql_id = s.sql_id
             AND q.child_number = s.sql_child_number
             AND ROWNUM = 1
       ) AS "sql_text",
       (
           SELECT SUBSTR(q.sql_text, 1, 4000)
           FROM v$sql q
           WHERE q.sql_id = s.prev_sql_id
             AND q.child_number = s.prev_child_number
             AND ROWNUM = 1
       ) AS "prev_sql_text"
FROM v$session s
WHERE s.sid = :sid
  AND s.serial# = :serial
  AND s.type = 'USER'
"""

_SESSION_SQL_IDS = """
SELECT sql_id AS "sql_id",
       prev_sql_id AS "prev_sql_id"
FROM v$session
WHERE sid = :sid
  AND serial# = :serial
  AND type = 'USER'
"""


def kill_session_sql(sid: int, serial: int) -> str:
    """Build ALTER SYSTEM KILL SESSION from integers only. The statement cannot use binds."""
    for label, value in (("sid", sid), ("serial", serial)):
        if type(value) is not int or value < 1 or value > 999_999_999:
            raise AppError(f"The session {label} is not a valid number.")
    return f"ALTER SYSTEM KILL SESSION '{sid},{serial}' IMMEDIATE"


def _current_sid(fetch: Fetcher):
    try:
        found = fetch("""SELECT TO_NUMBER(SYS_CONTEXT('USERENV','SID')) AS "sid" FROM dual""")
    except QueryFailed:
        return None
    if not found:
        return None
    return found[0].get("sid")


def build_tablespaces(fetch: Fetcher) -> dict:
    notices: list[str] = []
    try:
        rows = fetch(_PERM_TABLESPACES)
        source = "dba_tablespaces"
        try:
            rows = rows + fetch(_TEMP_TABLESPACES)
        except QueryFailed as exc:
            if not is_missing_privilege(exc.message):
                raise
            notices.append(
                f"Temporary tablespace usage was not read ({first_line(exc.message)})."
            )
    except QueryFailed as exc:
        if not is_missing_privilege(exc.message):
            raise
        source = "user_tablespaces"
        notices.append(
            f"Instance tablespace views are not available ({first_line(exc.message)}). "
            "Showing segment and free-space figures visible to this user from "
            "USER_TABLESPACES, USER_SEGMENTS, and USER_FREE_SPACE. "
            "These numbers are not the whole instance. "
            "Grant SELECT on DBA_TABLESPACES, DBA_DATA_FILES, DBA_FREE_SPACE, and "
            "DBA_TEMP_FREE_SPACE for the full dashboard."
        )
        rows = _user_tablespaces(fetch, notices)
    return {
        "ok": True,
        "source": source,
        "notice": " ".join(notices) or None,
        "rows": rows,
        "summary": _tablespace_summary(rows),
    }


def _user_tablespaces(fetch: Fetcher, notices: list[str]) -> list[dict]:
    spaces = fetch(
        """
        SELECT tablespace_name AS "tablespace_name",
               contents AS "contents",
               status AS "status"
        FROM user_tablespaces
        ORDER BY tablespace_name
        """
    )
    try:
        used_rows = fetch(
            """
            SELECT tablespace_name AS "tablespace_name",
                   ROUND(SUM(bytes) / 1024 / 1024, 1) AS "used_mb"
            FROM user_segments
            GROUP BY tablespace_name
            """
        )
    except QueryFailed as exc:
        used_rows = []
        notices.append(f"USER_SEGMENTS was not read ({first_line(exc.message)}).")
    try:
        free_rows = fetch(
            """
            SELECT tablespace_name AS "tablespace_name",
                   ROUND(SUM(bytes) / 1024 / 1024, 1) AS "free_mb"
            FROM user_free_space
            GROUP BY tablespace_name
            """
        )
    except QueryFailed as exc:
        free_rows = []
        notices.append(f"USER_FREE_SPACE was not read ({first_line(exc.message)}).")
    used = {row["tablespace_name"]: row.get("used_mb") for row in used_rows}
    free = {row["tablespace_name"]: row.get("free_mb") for row in free_rows}
    rows = []
    for space in spaces:
        name = space["tablespace_name"]
        used_mb = used.get(name)
        free_mb = free.get(name)
        total = None
        pct = None
        if used_mb is not None and free_mb is not None:
            total = round(float(used_mb) + float(free_mb), 1)
            pct = round((float(used_mb) / total) * 100, 1) if total else 0
        rows.append(
            {
                "tablespace_name": name,
                "contents": space.get("contents"),
                "status": space.get("status"),
                "total_mb": total,
                "used_mb": used_mb,
                "free_mb": free_mb,
                "pct_used": pct,
                "max_mb": None,
                "files": None,
            }
        )
    return rows


def build_runtime(fetch: Fetcher) -> dict:
    sections = [
        _runtime_section(
            fetch,
            "instance",
            "Instance",
            """
            SELECT instance_name AS "instance_name",
                   host_name AS "host_name",
                   version AS "version",
                   status AS "status",
                   database_status AS "database_status",
                   TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI:SS') AS "startup_time"
            FROM v$instance
            """,
            "Grant SELECT on V_$INSTANCE to see instance startup and status.",
            _INSTANCE_FALLBACK,
        ),
        _runtime_section(
            fetch,
            "memory",
            "SGA",
            """
            SELECT name AS "name", ROUND(value / 1024 / 1024, 1) AS "mb"
            FROM v$sga
            ORDER BY value DESC
            """,
            "Grant SELECT on V_$SGA to see memory pools.",
        ),
        _runtime_section(
            fetch,
            "pga",
            "PGA",
            """
            SELECT name AS "name", ROUND(value / 1024 / 1024, 1) AS "mb"
            FROM v$pgastat
            WHERE name IN ('total PGA allocated', 'maximum PGA allocated', 'total PGA inuse')
            """,
            "Grant SELECT on V_$PGASTAT to see PGA use.",
        ),
        _runtime_section(
            fetch,
            "host",
            "Host and database load",
            """
            SELECT metric_name AS "metric_name",
                   ROUND(value, 1) AS "value",
                   metric_unit AS "metric_unit"
            FROM v$sysmetric
            WHERE group_id = 2
              AND metric_name IN (
                  'Host CPU Utilization (%)',
                  'Database CPU Time Ratio',
                  'Average Active Sessions',
                  'I/O Megabytes per Second'
              )
            ORDER BY metric_name
            """,
            "Grant SELECT on V_$SYSMETRIC to see the latest one-minute host and database load.",
        ),
        _runtime_section(
            fetch,
            "long-sql",
            "Long-running sessions",
            """
            SELECT sid AS "sid",
                   serial# AS "serial",
                   username AS "username",
                   last_call_et AS "seconds",
                   sql_id AS "sql_id",
                   event AS "event",
                   machine AS "machine",
                   program AS "program"
            FROM v$session
            WHERE type = 'USER'
              AND status = 'ACTIVE'
              AND username IS NOT NULL
              AND last_call_et >= 5
            ORDER BY last_call_et DESC
            """,
            "Grant SELECT on V_$SESSION to list sessions active for 5 seconds or more.",
        ),
        _runtime_section(
            fetch,
            "longops",
            "Long operations",
            """
            SELECT sid AS "sid",
                   opname AS "opname",
                   target AS "target",
                   sofar AS "sofar",
                   totalwork AS "totalwork",
                   ROUND(sofar / NULLIF(totalwork, 0) * 100, 1) AS "pct",
                   elapsed_seconds AS "elapsed_seconds",
                   time_remaining AS "time_remaining",
                   sql_id AS "sql_id"
            FROM v$session_longops
            WHERE sofar < totalwork
            ORDER BY time_remaining DESC
            """,
            "Grant SELECT on V_$SESSION_LONGOPS to see operations that are still running.",
        ),
        _runtime_section(
            fetch,
            "top-sql",
            "Top SQL by elapsed time",
            """
            SELECT sql_id AS "sql_id",
                   executions AS "executions",
                   ROUND(elapsed_time / 1000000, 1) AS "elapsed_s",
                   ROUND(cpu_time / 1000000, 1) AS "cpu_s",
                   buffer_gets AS "buffer_gets",
                   disk_reads AS "disk_reads",
                   SUBSTR(sql_text, 1, 180) AS "sql_text"
            FROM (
                SELECT sql_id, executions, elapsed_time, cpu_time, buffer_gets, disk_reads, sql_text
                FROM v$sql
                WHERE executions > 0
                ORDER BY elapsed_time DESC
            )
            WHERE ROWNUM <= 15
            """,
            "Grant SELECT on V_$SQL to see the statements that have used the most elapsed time.",
        ),
    ]
    return {"ok": True, "sections": sections}


_INSTANCE_FALLBACK = """
SELECT SYS_CONTEXT('USERENV','INSTANCE_NAME') AS "instance_name",
       SYS_CONTEXT('USERENV','SERVER_HOST') AS "host_name",
       SYS_CONTEXT('USERENV','DB_NAME') AS "db_name",
       SYS_CONTEXT('USERENV','SERVICE_NAME') AS "service_name",
       SYS_CONTEXT('USERENV','SESSION_USER') AS "session_user"
FROM dual
"""


def _runtime_section(
    fetch: Fetcher,
    section_id: str,
    title: str,
    sql: str,
    grant: str,
    fallback_sql: str | None = None,
) -> dict:
    try:
        rows = fetch(sql)
        return {"id": section_id, "title": title, "notice": None, "rows": rows}
    except QueryFailed as exc:
        if not is_missing_privilege(exc.message):
            return {
                "id": section_id,
                "title": title,
                "notice": f"{title} failed ({first_line(exc.message)}).",
                "rows": [],
            }
        notice = f"{title} is not available ({first_line(exc.message)}). {grant}"
        if fallback_sql:
            try:
                rows = fetch(fallback_sql)
            except QueryFailed as second:
                return {
                    "id": section_id,
                    "title": title,
                    "notice": notice + " " + first_line(second.message),
                    "rows": [],
                }
            return {
                "id": section_id,
                "title": title,
                "notice": notice + " Showing this session's own names instead.",
                "rows": rows,
            }
        return {"id": section_id, "title": title, "notice": notice, "rows": []}


def _session_summary(rows: list[dict]) -> dict:
    blocked = 0
    active = 0
    inactive = 0
    for row in rows:
        status = str(row.get("status") or "").upper()
        if status == "ACTIVE":
            active += 1
        elif status == "INACTIVE":
            inactive += 1
        if row.get("blocking_session") not in (None, "", 0):
            blocked += 1
    return {"total": len(rows), "active": active, "inactive": inactive, "blocked": blocked}


def _tablespace_summary(rows: list[dict]) -> dict:
    warning = 0
    critical = 0
    used = 0.0
    have_used = False
    for row in rows:
        pct = row.get("pct_used")
        if pct is not None:
            if float(pct) >= 95:
                critical += 1
            elif float(pct) >= 85:
                warning += 1
        if row.get("used_mb") is not None:
            used += float(row["used_mb"])
            have_used = True
    return {
        "count": len(rows),
        "warning": warning,
        "critical": critical,
        "used_mb": round(used, 1) if have_used else None,
    }
