"""JSON API for connections, the schema browser, the worksheet, and dashboards."""

from __future__ import annotations

import logging
from typing import Any, Literal

import oracledb
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.audit import audit
from app.catalog import browse_rows, describe_object, list_objects, list_owners
from app.db import EXPIRE_TIME_MINUTES, connection_lost, describe_session, fetch_dicts
from app.errors import AppError, QueryFailed, first_line
from app.monitor import build_runtime, build_session_sql, build_sessions, build_tablespaces, kill_session_sql
from app.security import check_csrf, client_ip, rotate_session, verify_admin_password
from app.worksheet import execute_script, fetch_next_page

log = logging.getLogger("e1p.api")
router = APIRouter()


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=1024)


class ConnectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    username: str
    password: str = ""
    mode: Literal["wallet", "classic", "connect_string"]
    host: str = ""
    port: int | None = None
    service_name: str = ""
    sid: str = ""
    protocol: Literal["tcp", "tcps"] = "tcp"
    ssl_server_dn_match: bool = True
    wallet_dir: str = ""
    wallet_password: str = ""
    tns_alias: str = ""
    connect_string: str = ""
    role: Literal["", "SYSDBA", "SYSOPER"] = ""
    read_only: bool = False
    confirm_rollback: bool = False


class TestIn(ConnectionIn):
    id: str | None = None


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_rollback: bool = False


class RowsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str
    name: str
    where: str = ""
    column: str = ""
    op: Literal["", "eq", "ne", "contains", "starts", "null", "notnull"] = ""
    value: str = ""
    limit: int = Field(default=100, ge=1, le=2000)


class ExecuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=1_000_000)
    max_rows: int = Field(default=50, ge=1, le=500)
    autocommit: bool = False
    binds: dict[str, Any] | None = None
    confirm_ddl: bool = False


class PrefsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_locking_sql: bool
    rollback_script_on_error: bool
    max_open_connections: int = Field(ge=1, le=30)
    tag_sessions: bool


class PageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=8, max_length=80)


class BucketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    sql: str = ""


def _action(request: Request, name: str) -> str | None:
    if request.app.state.prefs.get()["tag_sessions"]:
        return name
    return None


def _user(request: Request) -> str:
    if not request.session.get("auth") or not request.session.get("sid"):
        raise AppError("Sign in required.", 401)
    return str(request.session["sid"])


def _mutate(request: Request) -> str:
    sid = _user(request)
    check_csrf(request)
    return sid


@router.post("/api/login")
def login(body: LoginIn, request: Request) -> dict:
    check_csrf(request)
    settings = request.app.state.settings
    ip = client_ip(request)
    request.app.state.limiter.check(ip)
    if not verify_admin_password(body.password, settings.admin_password_hash):
        request.app.state.limiter.fail(ip)
        audit(settings, "login_fail", ip=ip)
        raise AppError("Incorrect password.", 401)
    request.app.state.limiter.clear(ip)
    token = rotate_session(request)
    audit(settings, "login_ok", ip=ip)
    return {"ok": True, "csrf_token": token}


@router.post("/api/logout")
def logout(request: Request) -> dict:
    sid = _mutate(request)
    request.app.state.registry.close_browser(sid)
    for key in list(request.session.keys()):
        request.session.pop(key, None)
    return {"ok": True}


@router.get("/api/workspace")
def workspace(request: Request) -> dict:
    sid = _user(request)
    settings = request.app.state.settings
    registry = request.app.state.registry
    opened = registry.open_sessions(sid)
    handle = registry.get(sid)
    return {
        "connected": handle is not None,
        "active_connection_id": handle.connection_id if handle else None,
        "connection_name": handle.name if handle else None,
        "db_user": handle.db_user if handle else None,
        "db_name": handle.db_name if handle else None,
        "service_name": handle.service_name if handle else None,
        "read_only": handle.read_only if handle else False,
        "open_transaction": handle.open_transaction if handle else False,
        "idle_rollback_note": registry.take_idle_note(sid),
        "open_connections": opened,
        "wallet_root": str(settings.wallet_root) if settings.wallet_root else None,
        "max_fetch_rows": settings.max_fetch_rows,
    }


@router.get("/api/client-prefs")
def client_prefs(request: Request) -> dict:
    _user(request)
    return request.app.state.prefs.get()


@router.put("/api/client-prefs")
def update_client_prefs(body: PrefsIn, request: Request) -> dict:
    _mutate(request)
    return request.app.state.prefs.update(body.model_dump())


@router.get("/api/connections")
def connections(request: Request) -> dict:
    _user(request)
    return {"connections": request.app.state.store.list_public()}


@router.post("/api/connections")
def create_connection(body: ConnectionIn, request: Request) -> dict:
    _mutate(request)
    created = request.app.state.store.create(body.model_dump())
    audit(request.app.state.settings, "connection_create", name=created["name"], mode=created["mode"])
    return created


@router.put("/api/connections/{connection_id}")
def update_connection(connection_id: str, body: ConnectionIn, request: Request) -> dict:
    _mutate(request)
    store = request.app.state.store
    registry = request.app.state.registry
    current = store.get(connection_id)
    compiled, _secrets = store.compile(body.model_dump(), connection_id)
    from app.store import fingerprint

    changed = fingerprint(current) != fingerprint(compiled)
    rolled = False
    if changed:
        rolled = registry.drop_connection(connection_id, body.confirm_rollback)
    updated, changed = store.update(connection_id, body.model_dump())
    if not changed:
        registry.update_labels(connection_id, updated["name"], updated["read_only"])
    audit(request.app.state.settings, "connection_update", name=updated["name"], reconnected=changed)
    return {"connection": updated, "reconnected": changed, "rolled_back": rolled, "previous_name": current["name"]}


@router.delete("/api/connections/{connection_id}")
def delete_connection(connection_id: str, body: ConfirmIn, request: Request) -> dict:
    _mutate(request)
    rolled = request.app.state.registry.drop_connection(connection_id, body.confirm_rollback)
    request.app.state.store.delete(connection_id)
    if request.session.get("active_connection_id") == connection_id:
        request.session.pop("active_connection_id", None)
    audit(request.app.state.settings, "connection_delete", connection_id=connection_id, rolled_back=rolled)
    return {"ok": True, "rolled_back": rolled}


@router.post("/api/connections/test")
def test_connection(body: TestIn, request: Request) -> dict:
    _mutate(request)
    record, secrets = request.app.state.store.compile(body.model_dump(), body.id)
    connection = None
    try:
        from app.db import open_connection

        connection = open_connection(record, secrets, request.app.state.settings)
        info = describe_session(connection)
        banner = _banner(connection)
        audit(
            request.app.state.settings,
            "connection_test",
            name=record["name"],
            db_user=info.get("session_user") or record["username"],
            ok=True,
        )
        return {
            "ok": True,
            "session_user": info.get("session_user") or record["username"],
            "db_name": info.get("db_name") or "",
            "service_name": info.get("service_name") or "",
            "banner": banner,
        }
    except AppError:
        raise
    finally:
        if connection is not None:
            try:
                connection.close()
            except oracledb.Error:
                pass


@router.post("/api/connections/{connection_id}/connect")
def connect(connection_id: str, body: ConfirmIn, request: Request) -> dict:
    sid = _mutate(request)
    store = request.app.state.store
    record = store.get(connection_id)
    secrets = store.secrets_for(record)
    prefs = request.app.state.prefs.get()
    handle, rolled = request.app.state.registry.connect(
        sid,
        record,
        secrets,
        body.confirm_rollback,
        max_open=prefs["max_open_connections"],
        expire_time=EXPIRE_TIME_MINUTES if prefs["tag_sessions"] else None,
    )
    request.session["active_connection_id"] = connection_id
    audit(
        request.app.state.settings,
        "connect",
        name=handle.name,
        db_user=handle.db_user,
        rolled_back=rolled,
    )
    return {
        "ok": True,
        "connection_id": handle.connection_id,
        "connection_name": handle.name,
        "db_user": handle.db_user,
        "db_name": handle.db_name,
        "service_name": handle.service_name,
        "read_only": handle.read_only,
        "open_transaction": handle.open_transaction,
        "rolled_back": rolled,
    }


@router.post("/api/connections/{connection_id}/activate")
def activate(connection_id: str, request: Request) -> dict:
    sid = _mutate(request)
    handle = request.app.state.registry.activate(sid, connection_id)
    request.session["active_connection_id"] = handle.connection_id
    return {
        "ok": True,
        "connection_id": handle.connection_id,
        "connection_name": handle.name,
        "db_user": handle.db_user,
        "db_name": handle.db_name,
        "service_name": handle.service_name,
        "read_only": handle.read_only,
    }


@router.post("/api/connections/{connection_id}/disconnect")
def disconnect_one(connection_id: str, body: ConfirmIn, request: Request) -> dict:
    sid = _mutate(request)
    rolled = request.app.state.registry.disconnect(sid, body.confirm_rollback, connection_id)
    audit(request.app.state.settings, "disconnect", connection_id=connection_id, rolled_back=rolled)
    return {"ok": True, "rolled_back": rolled}


@router.post("/api/connections/disconnect")
def disconnect(body: ConfirmIn, request: Request) -> dict:
    sid = _mutate(request)
    rolled = request.app.state.registry.disconnect(sid, body.confirm_rollback)
    audit(request.app.state.settings, "disconnect", rolled_back=rolled)
    return {"ok": True, "rolled_back": rolled}


@router.get("/api/schema/owners")
def owners(request: Request) -> dict:
    sid = _user(request)
    with request.app.state.registry.session(sid, _action(request, "Data")) as handle:
        try:
            return list_owners(handle.conn)
        except AppError as exc:
            _reraise(handle, "Schema browser", exc)
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, "Schema browser")


@router.get("/api/schema/objects")
def objects(request: Request, owner: str, type: str, q: str = "") -> dict:
    sid = _user(request)
    with request.app.state.registry.session(sid, _action(request, "Data")) as handle:
        try:
            return list_objects(handle.conn, owner, type, q)
        except AppError as exc:
            _reraise(handle, "Schema browser", exc)
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, "Schema browser")


@router.get("/api/schema/object")
def object_detail(request: Request, owner: str, name: str, type: str) -> dict:
    sid = _user(request)
    with request.app.state.registry.session(sid, _action(request, "Data")) as handle:
        try:
            return describe_object(handle.conn, owner, name, type)
        except AppError as exc:
            _reraise(handle, "Object browser", exc)
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, "Object browser")


@router.post("/api/schema/rows")
def schema_rows(body: RowsIn, request: Request) -> dict:
    sid = _mutate(request)
    limit = min(body.limit, request.app.state.settings.max_fetch_rows)
    with request.app.state.registry.session(sid, _action(request, "Data")) as handle:
        try:
            return browse_rows(
                handle.conn,
                body.owner,
                body.name,
                body.where,
                body.column,
                body.op,
                body.value,
                limit,
            )
        except AppError as exc:
            _reraise(handle, f"Data for {body.owner}.{body.name}", exc)
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, f"Data for {body.owner}.{body.name}")


@router.post("/api/sql/execute")
def execute(body: ExecuteIn, request: Request) -> dict:
    sid = _mutate(request)
    settings = request.app.state.settings
    registry = request.app.state.registry
    prefs = request.app.state.prefs.get()
    with registry.session(sid, _action(request, "Worksheet")) as handle:
        record = request.app.state.store.get(handle.connection_id)
        secrets = request.app.state.store.secrets_for(record)
        secret_values = [secrets["password"], secrets["wallet_password"]]
        limit = min(body.max_rows, settings.max_fetch_rows)
        try:
            result = execute_script(
                handle,
                body.sql,
                limit,
                body.autocommit,
                body.binds,
                secret_values,
                confirm_ddl=body.confirm_ddl,
                block_locking_sql=prefs["block_locking_sql"],
                rollback_script_on_error=prefs["rollback_script_on_error"],
            )
        except oracledb.Error as exc:
            if connection_lost(exc):
                registry.discard(sid)
                raise AppError(
                    f"Worksheet on {handle.name} ({handle.db_user}): the database session ended. "
                    + first_line(str(exc)),
                    409,
                ) from exc
            raise AppError(
                f"Worksheet on {handle.name} ({handle.db_user}): {first_line(str(exc))}",
                400,
            ) from exc
    error = result.get("error") or {}
    if error.get("message"):
        prefix = f"Worksheet on {handle.name} ({handle.db_user})"
        if error.get("statement"):
            prefix += f", statement {error['statement']}"
        error["message"] = prefix + ": " + error["message"]
        result["connection_name"] = handle.name
    audit_fields = {
        "name": handle.name,
        "statements": len(result.get("results") or []),
        "ok": result.get("ok"),
        "error_code": _error_code(error.get("message", "")),
    }
    if settings.audit_sql_text:
        audit_fields["sql"] = body.sql[:4000]
    audit(settings, "sql", **audit_fields)
    return result


@router.post("/api/sql/page")
def sql_page(body: PageIn, request: Request) -> dict:
    sid = _mutate(request)
    with request.app.state.registry.session(sid, _action(request, "Worksheet")) as handle:
        try:
            page = fetch_next_page(handle, body.token)
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, "Next page")
        page["connection_name"] = handle.name
        return page


@router.get("/api/library")
def library_list(request: Request) -> dict:
    _user(request)
    return {"buckets": request.app.state.library.list_buckets()}


@router.post("/api/library/buckets")
def library_create_bucket(body: BucketIn, request: Request) -> dict:
    _mutate(request)
    return request.app.state.library.create_bucket(body.name)


@router.put("/api/library/buckets/{bucket_id}")
def library_rename_bucket(bucket_id: str, body: BucketIn, request: Request) -> dict:
    _mutate(request)
    return request.app.state.library.rename_bucket(bucket_id, body.name)


@router.delete("/api/library/buckets/{bucket_id}")
def library_delete_bucket(bucket_id: str, request: Request) -> dict:
    _mutate(request)
    request.app.state.library.delete_bucket(bucket_id)
    return {"ok": True}


@router.post("/api/library/buckets/{bucket_id}/queries")
def library_save_query(bucket_id: str, body: QueryIn, request: Request) -> dict:
    _mutate(request)
    return request.app.state.library.save_query(bucket_id, body.title, body.sql)


@router.put("/api/library/queries/{query_id}")
def library_update_query(query_id: str, body: QueryIn, request: Request) -> dict:
    _mutate(request)
    return request.app.state.library.update_query(query_id, body.title, body.sql)


@router.delete("/api/library/queries/{query_id}")
def library_delete_query(query_id: str, request: Request) -> dict:
    _mutate(request)
    request.app.state.library.delete_query(query_id)
    return {"ok": True}


@router.post("/api/sql/commit")
def commit(request: Request) -> dict:
    sid = _mutate(request)
    with request.app.state.registry.session(sid, _action(request, "Worksheet")) as handle:
        if handle.read_only:
            raise AppError("This connection is read only. Commit is blocked.", 403)
        try:
            handle.conn.commit()
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, "Commit")
        audit(request.app.state.settings, "commit", name=handle.name)
        return {"ok": True, "open_transaction": handle.open_transaction}


@router.post("/api/sql/rollback")
def rollback(request: Request) -> dict:
    sid = _mutate(request)
    with request.app.state.registry.session(sid, _action(request, "Worksheet")) as handle:
        try:
            handle.conn.rollback()
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, "Rollback")
        audit(request.app.state.settings, "rollback", name=handle.name)
        return {"ok": True, "open_transaction": handle.open_transaction}


@router.get("/api/monitor/sessions")
def sessions(request: Request) -> dict:
    return _monitor(request, build_sessions, "Sessions", "Sessions")


class KillSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sid: int = Field(ge=1, le=999_999_999)
    serial: int = Field(ge=1, le=999_999_999)


@router.post("/api/monitor/sessions/kill")
def kill_session(body: KillSessionIn, request: Request) -> dict:
    sid = _mutate(request)
    statement = kill_session_sql(body.sid, body.serial)
    with request.app.state.registry.session(sid, _action(request, "Sessions")) as handle:
        if handle.read_only:
            raise AppError("This connection is read only. Kill session is blocked.", 403)
        try:
            cursor = handle.conn.cursor()
            try:
                cursor.execute(statement)
            finally:
                cursor.close()
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, "Kill session")
        audit(
            request.app.state.settings,
            "kill_session",
            name=handle.name,
            db_user=handle.db_user,
            sid=body.sid,
            serial=body.serial,
        )
        return {
            "ok": True,
            "message": f"Oracle accepted the kill for session {body.sid},{body.serial}.",
        }


@router.get("/api/monitor/tablespaces")
def tablespaces(request: Request) -> dict:
    return _monitor(request, build_tablespaces, "Tablespaces", "Tablespaces")


@router.get("/api/monitor/runtime")
def runtime(request: Request) -> dict:
    return _monitor(request, build_runtime, "Runtime dashboard", "Runtime")


@router.get("/api/monitor/sessions/sql")
def session_sql(
    request: Request,
    sid: int = Query(ge=1, le=999_999_999),
    serial: int = Query(ge=1, le=999_999_999),
) -> dict:
    browser = _user(request)

    def run(fetch_binds):
        return build_session_sql(fetch_binds, sid, serial)

    with request.app.state.registry.session(browser, _action(request, "Sessions")) as handle:
        def fetch_binds(sql: str, binds: dict) -> list[dict]:
            return fetch_dicts(handle.conn, sql, binds)

        try:
            report = run(fetch_binds)
        except QueryFailed as exc:
            raise AppError(
                f"Session SQL on {handle.name} ({handle.db_user}): {first_line(exc.message)}",
                400,
            ) from exc
        except oracledb.Error as exc:
            _raise_oracle(request, browser, exc, "Session SQL")
        return report


@router.post("/api/sql/cancel")
def cancel_sql(request: Request) -> dict:
    sid = _mutate(request)
    request.app.state.registry.cancel(sid)
    return {"ok": True, "message": "Cancel sent. Uncommitted work is still open."}


@router.post("/api/connections/close-browser")
def close_browser(request: Request) -> dict:
    sid = _mutate(request)
    request.app.state.registry.close_browser(sid)
    return {"ok": True}


def _monitor(request: Request, builder, tool: str, action: str) -> dict:
    sid = _user(request)
    with request.app.state.registry.session(sid, _action(request, action)) as handle:
        def fetch(sql: str) -> list[dict]:
            return fetch_dicts(handle.conn, sql)

        try:
            report = builder(fetch)
        except QueryFailed as exc:
            if connection_lost(exc):
                request.app.state.registry.discard(sid)
                raise AppError(
                    f"{tool} on {handle.name} ({handle.db_user}): the database session ended. "
                    + first_line(exc.message),
                    409,
                ) from exc
            raise AppError(
                f"{tool} on {handle.name} ({handle.db_user}): {first_line(exc.message)}",
                400,
            ) from exc
        except oracledb.Error as exc:
            _raise_oracle(request, sid, exc, tool)
        report["connection_name"] = handle.name
        report["db_user"] = handle.db_user
        return report


def _banner(connection) -> str:
    try:
        rows = fetch_dicts(
            connection,
            """
            SELECT banner AS "banner"
            FROM v$version
            WHERE ROWNUM = 1
            """,
        )
    except QueryFailed:
        return ""
    if not rows:
        return ""
    return str(rows[0].get("banner") or "")


def _reraise(handle, tool: str, exc: AppError) -> None:
    raise AppError(
        f"{tool} on {handle.name} ({handle.db_user}): {exc.message}",
        exc.status,
        exc.extra,
    ) from exc


def _raise_oracle(request: Request, sid: str, exc: BaseException, tool: str = "Database") -> None:
    handle = request.app.state.registry.get(sid)
    name = f"{handle.name} ({handle.db_user})" if handle else "the working connection"
    if connection_lost(exc):
        request.app.state.registry.discard(sid)
        raise AppError(
            f"{tool} on {name}: the database session ended. Connect again. {first_line(str(exc))}",
            409,
        ) from exc
    raise AppError(f"{tool} on {name}: {first_line(str(exc))}", 400) from exc


def _error_code(message: str) -> str:
    for token in message.split():
        if token.startswith("ORA-") or token.startswith("DPY-"):
            return token.rstrip(":")
    return ""
