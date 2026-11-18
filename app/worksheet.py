"""Run a worksheet script on the pinned Oracle session."""

from __future__ import annotations

import secrets
import time
from typing import Any

import oracledb

from app.db import DbHandle, connection_lost, jsonable
from app.errors import AppError, redact
from app.sqlsplit import is_locking_sql, needs_ddl_confirm, prepare_for_driver, split_script, statement_kind


class BindsRequired(Exception):
    def __init__(self, names: list[str]) -> None:
        super().__init__(", ".join(names))
        self.names = names


def ensure_allowed(statements: list, read_only: bool) -> None:
    if not read_only:
        return
    for index, item in enumerate(statements, start=1):
        if item.kind != "sql":
            continue
        if statement_kind(item.text) != "query":
            raise AppError(
                f"Statement {index} is not a query. This connection is read only, "
                "so DML, DDL, and PL/SQL are blocked.",
                403,
            )


def execute_script(
    handle: DbHandle,
    sql: str,
    max_rows: int,
    autocommit: bool,
    binds: dict[str, Any] | None,
    secrets: list[str],
    *,
    confirm_ddl: bool = False,
    block_locking_sql: bool = True,
    rollback_script_on_error: bool = True,
) -> dict:
    items = split_script(sql)
    if not items:
        raise AppError("Enter a SQL statement or PL/SQL block.")
    for item in items:
        if item.kind == "unsupported":
            raise AppError(
                f"Unsupported SQL*Plus command: {item.text}. "
                "Remove it and run the SQL or PL/SQL directly. "
                "SET SERVEROUTPUT and PROMPT are accepted."
            )
    ensure_allowed(items, handle.read_only)
    if block_locking_sql:
        for index, item in enumerate(items, start=1):
            if item.kind == "sql" and is_locking_sql(item.text) and handle.read_only:
                raise AppError(
                    f"Statement {index} locks rows (SELECT FOR UPDATE or LOCK TABLE). "
                    "This connection is read only, and Settings blocks locking SQL.",
                    403,
                )
    if any(item.kind == "sql" and needs_ddl_confirm(item.text) for item in items) and not confirm_ddl:
        raise AppError(
            "This script includes DROP, TRUNCATE, GRANT, REVOKE, CREATE, PURGE, "
            "FLASHBACK, RENAME, or ALTER (other than ALTER SESSION). "
            "It can change or remove data and objects. Confirm to run it.",
            409,
            {"code": "confirm_ddl"},
        )
    _close_pages(handle)
    clean_binds = _coerce_binds(binds)
    page_size = _page_size(max_rows)
    connection = handle.conn
    connection.autocommit = bool(autocommit)
    _set_serveroutput(connection, True)
    collect_output = True
    results: list[dict] = []
    try:
        for index, item in enumerate(items, start=1):
            if item.kind == "serveroutput":
                collect_output = item.enabled
                _set_serveroutput(connection, item.enabled)
                results.append(
                    {
                        "index": index,
                        "kind": "message",
                        "text": f"SERVEROUTPUT {item.text}",
                    }
                )
                continue
            if item.kind == "prompt":
                results.append({"index": index, "kind": "message", "text": item.text})
                continue
            try:
                results.append(
                    _run_one(
                        handle,
                        index,
                        item.text,
                        page_size,
                        clean_binds,
                        keep_page=not (block_locking_sql and is_locking_sql(item.text)),
                    )
                )
            except BindsRequired as exc:
                return _fail(
                    handle,
                    connection,
                    rollback_script_on_error,
                    False,
                    results,
                    collect_output,
                    {
                        "message": "Statement "
                        + str(index)
                        + " needs bind values for: "
                        + ", ".join(exc.names)
                        + ". Enter them as a JSON object, for example {\"id\": 10}.",
                        "statement": index,
                    },
                    index,
                    code="binds_required",
                    bind_names=exc.names,
                )
            except oracledb.Error as exc:
                if connection_lost(exc):
                    raise
                message = redact(str(exc).strip(), secrets)
                return _fail(
                    handle,
                    connection,
                    rollback_script_on_error,
                    _cancelled(exc),
                    results,
                    collect_output,
                    {"message": message, "statement": index},
                    index,
                )
    except oracledb.Error as exc:
        if connection_lost(exc):
            raise
        return _fail(
            handle,
            connection,
            rollback_script_on_error,
            _cancelled(exc),
            results,
            collect_output,
            {"message": redact(str(exc).strip(), secrets), "statement": None},
            None,
        )
    payload = _done(handle, ok=True, results=results, collect_output=collect_output, error=None, stopped_at=None)
    payload["rolled_back"] = False
    return payload


def fetch_next_page(handle: DbHandle, token: str) -> dict:
    slot = handle.page_cursors.get(token)
    if slot is None:
        raise AppError(
            "This result page is no longer open. Run the query again. "
            "Only the rows already fetched stay in the browser."
        )
    cursor = slot["cursor"]
    page_size = slot["page_size"]
    try:
        fetched = cursor.fetchmany(page_size)
    except oracledb.Error as exc:
        _drop_page(handle, token)
        raise AppError(
            "The next page could not be read. Run the query again. " + str(exc).strip().splitlines()[0]
        ) from exc
    has_more = len(fetched) == page_size
    visible = fetched
    columns = list(slot["columns"])
    if cursor.description:
        columns = [item[0] for item in cursor.description]
    slot["page"] += 1
    slot["touched"] = time.time()
    start = slot["fetched"] + 1
    slot["fetched"] += len(visible)
    if not has_more:
        _drop_page(handle, token)
    return {
        "columns": columns,
        "rows": [[jsonable(value) for value in row] for row in visible],
        "row_count": len(visible),
        "has_more": has_more,
        "page_token": token if has_more else None,
        "page_size": page_size,
        "page": slot["page"],
        "row_start": start,
    }


def _run_one(
    handle: DbHandle,
    index: int,
    sql: str,
    page_size: int,
    binds: dict,
    *,
    keep_page: bool = True,
) -> dict:
    statement = prepare_for_driver(sql)
    if not statement:
        raise AppError(f"Statement {index} is empty.")
    connection = handle.conn
    cursor = connection.cursor()
    kept = False
    try:
        cursor.prepare(statement)
        needed = _bind_names(cursor)
        params: dict = {}
        if needed:
            missing = []
            folded = {key.upper(): value for key, value in binds.items()}
            for name in needed:
                if name.upper() not in folded:
                    missing.append(name)
                else:
                    params[name] = folded[name.upper()]
            if missing:
                raise BindsRequired(missing)
        cursor.arraysize = page_size
        started = time.perf_counter()
        cursor.execute(statement, params or None)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        if cursor.description:
            columns = [item[0] for item in cursor.description]
            fetched = cursor.fetchmany(page_size)
            has_more = len(fetched) == page_size
            rows = [[jsonable(value) for value in row] for row in fetched]
            token = None
            paging_closed = False
            if has_more and keep_page:
                token = secrets.token_urlsafe(18)
                handle.page_cursors[token] = {
                    "cursor": cursor,
                    "page_size": page_size,
                    "page": 1,
                    "fetched": len(rows),
                    "columns": columns,
                    "touched": time.time(),
                }
                kept = True
            elif has_more:
                has_more = False
                paging_closed = True
            return {
                "index": index,
                "kind": "query",
                "summary": _summary(statement),
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": has_more,
                "has_more": has_more,
                "page_token": token,
                "page_size": page_size,
                "page": 1,
                "row_start": 1 if rows else 0,
                "elapsed_ms": elapsed,
                "paging_closed": paging_closed,
            }
        kind = statement_kind(statement)
        row_count = cursor.rowcount
        if row_count is None or row_count < 0:
            row_count = None
        return {
            "index": index,
            "kind": kind,
            "summary": _summary(statement),
            "row_count": row_count,
            "elapsed_ms": elapsed,
        }
    finally:
        if not kept:
            cursor.close()


def _fail(
    handle: DbHandle,
    connection,
    rollback_script: bool,
    cancelled: bool,
    results: list[dict],
    collect_output: bool,
    error: dict,
    stopped_at: int | None,
    code: str | None = None,
    bind_names: list[str] | None = None,
) -> dict:
    rolled = False
    if rollback_script and not cancelled:
        try:
            connection.rollback()
            rolled = True
        except oracledb.Error:
            rolled = False
    payload = _done(
        handle,
        ok=False,
        results=results,
        collect_output=collect_output,
        error=error,
        stopped_at=stopped_at,
        code=code,
        bind_names=bind_names,
    )
    payload["rolled_back"] = rolled
    if cancelled and error.get("message"):
        error["message"] = (
            "The statement was cancelled. Uncommitted work is still open. " + error["message"]
        )
    return payload


def _cancelled(exc: BaseException) -> bool:
    return "ORA-01013" in str(exc).upper()


def _done(
    handle: DbHandle,
    ok: bool,
    results: list[dict],
    collect_output: bool,
    error: dict | None,
    stopped_at: int | None,
    code: str | None = None,
    bind_names: list[str] | None = None,
) -> dict:
    output: list[str] = []
    if collect_output:
        output = _read_output(handle.conn)
    payload = {
        "ok": ok,
        "results": results,
        "dbms_output": output,
        "open_transaction": handle.open_transaction,
        "error": error,
        "stopped_at": stopped_at,
    }
    if code:
        payload["code"] = code
        payload["binds"] = bind_names or []
    return payload


def _page_size(value: int) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = 50
    return min(max(size, 1), 500)


def _close_pages(handle: DbHandle) -> None:
    for token in list(handle.page_cursors):
        _drop_page(handle, token)


def _drop_page(handle: DbHandle, token: str) -> None:
    slot = handle.page_cursors.pop(token, None)
    if slot is None:
        return
    try:
        slot["cursor"].close()
    except Exception:
        pass


def _bind_names(cursor) -> list[str]:
    try:
        names = list(cursor.bindnames() or [])
    except oracledb.Error:
        return []
    unique: list[str] = []
    for name in names:
        if name not in unique:
            unique.append(name)
    return unique


def _coerce_binds(binds: dict | None) -> dict:
    if not binds:
        return {}
    if len(binds) > 100:
        raise AppError("Provide at most 100 bind values.")
    cleaned = {}
    for key, value in binds.items():
        if not isinstance(key, str) or not key:
            raise AppError("Bind names must be strings.")
        cleaned[key] = _coerce_bind(value)
    return cleaned


def _coerce_bind(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if value is None or isinstance(value, (int, float, str)):
        if isinstance(value, str) and len(value) > 100_000:
            raise AppError("A bind value is longer than 100000 characters.")
        return value
    raise AppError("Bind values must be strings, numbers, true, false, or null.")


def _set_serveroutput(connection, enabled: bool) -> None:
    cursor = connection.cursor()
    try:
        if enabled:
            cursor.callproc("dbms_output.enable", [1_000_000])
        else:
            cursor.callproc("dbms_output.disable")
    except oracledb.Error:
        pass
    finally:
        cursor.close()


def _read_output(connection) -> list[str]:
    lines: list[str] = []
    cursor = connection.cursor()
    try:
        while len(lines) < 2000:
            chunk = cursor.arrayvar(str, 100)
            count = cursor.var(int)
            count.setvalue(0, 100)
            cursor.callproc("dbms_output.get_lines", (chunk, count))
            fetched = count.getvalue() or 0
            if fetched:
                lines.extend(list(chunk.getvalue())[:fetched])
            if fetched < 100:
                break
    except oracledb.Error as exc:
        lines.append(f"(DBMS_OUTPUT was not read: {exc})")
    finally:
        cursor.close()
    if len(lines) >= 2000:
        lines.append("… DBMS_OUTPUT truncated at 2000 lines.")
    return lines


def _summary(sql: str) -> str:
    return " ".join(sql.split())[:160]
