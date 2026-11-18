"""Schema browser queries. Names that reach SQL text are allow-listed and quoted."""

from __future__ import annotations

import re

import oracledb

from app.db import fetch_dicts, jsonable
from app.errors import AppError, QueryFailed, first_line, is_missing_privilege

_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_$#]{0,127}")
_OBJECT_TYPES = {
    "TABLE",
    "VIEW",
    "MATERIALIZED VIEW",
    "INDEX",
    "SEQUENCE",
    "PROCEDURE",
    "FUNCTION",
    "PACKAGE",
    "PACKAGE BODY",
    "TRIGGER",
    "SYNONYM",
    "TYPE",
    "TYPE BODY",
}
_BROWSABLE = {"TABLE", "VIEW", "MATERIALIZED VIEW"}
_SOURCE = {"PROCEDURE", "FUNCTION", "PACKAGE", "PACKAGE BODY", "TRIGGER", "TYPE", "TYPE BODY"}


def list_owners(connection: oracledb.Connection) -> dict:
    notice = None
    source = "all_users"
    try:
        rows = fetch_dicts(
            connection,
            """
            SELECT username AS "owner"
            FROM all_users
            ORDER BY username
            """,
        )
        owners = [row["owner"] for row in rows][:5000]
    except QueryFailed as exc:
        if not is_missing_privilege(exc.message):
            raise AppError(first_line(exc.message), 400) from exc
        try:
            me = fetch_dicts(connection, 'SELECT USER AS "owner" FROM dual')
        except QueryFailed as second:
            raise AppError(first_line(second.message), 400) from second
        owners = [me[0]["owner"]] if me else []
        source = "current_user"
        notice = (
            f"ALL_USERS is not available ({first_line(exc.message)}). "
            "Showing only the connected user."
        )
    try:
        current_rows = fetch_dicts(connection, 'SELECT USER AS "owner" FROM dual')
    except QueryFailed as exc:
        raise AppError(first_line(exc.message), 400) from exc
    current = current_rows[0]["owner"] if current_rows else ""
    return {
        "owners": owners,
        "current_user": current,
        "source": source,
        "notice": notice,
        "truncated": source == "all_users" and len(rows) > 5000,
    }


def list_objects(connection: oracledb.Connection, owner: str, object_type: str, query: str) -> dict:
    owner = _ident(owner, "owner")
    object_type = object_type.strip().upper()
    if object_type not in _OBJECT_TYPES:
        raise AppError("That object type is not available in the browser.")
    sql = """
        SELECT object_name AS "object_name",
               object_type AS "object_type",
               status AS "status"
        FROM (
            SELECT object_name, object_type, status
            FROM all_objects
            WHERE owner = :owner
              AND object_type = :object_type
    """
    binds: dict = {"owner": owner, "object_type": object_type}
    prefix = _like_prefix(query)
    if prefix:
        sql += " AND object_name LIKE :prefix ESCAPE '\\'\n"
        binds["prefix"] = prefix
    sql += """
            ORDER BY object_name
        )
        WHERE ROWNUM <= 5001
    """
    try:
        rows = fetch_dicts(connection, sql, binds)
    except QueryFailed as exc:
        raise AppError(first_line(exc.message), 400) from exc
    truncated = len(rows) > 5000
    return {"objects": rows[:5000], "truncated": truncated, "notice": None}


def describe_object(
    connection: oracledb.Connection, owner: str, name: str, object_type: str
) -> dict:
    owner = _ident(owner, "owner")
    name = _ident(name, "object")
    object_type = object_type.strip().upper()
    if object_type not in _OBJECT_TYPES:
        raise AppError("That object type is not available in the browser.")
    if object_type in _BROWSABLE:
        return _describe_table(connection, owner, name, object_type)
    if object_type in _SOURCE:
        return _describe_source(connection, owner, name, object_type)
    try:
        rows = fetch_dicts(
            connection,
            """
            SELECT object_name AS "object_name", object_type AS "object_type", status AS "status"
            FROM all_objects
            WHERE owner = :owner AND object_name = :name AND object_type = :object_type
              AND ROWNUM = 1
            """,
            {"owner": owner, "name": name, "object_type": object_type},
        )
    except QueryFailed as exc:
        raise AppError(first_line(exc.message), 400) from exc
    if not rows:
        raise AppError(f"{object_type} {owner}.{name} is not visible to this user.", 404)
    return {"kind": "meta", "object": rows[0], "notice": None}


def _describe_table(connection: oracledb.Connection, owner: str, name: str, object_type: str) -> dict:
    notices: list[str] = []
    columns = _optional(
        connection,
        """
        SELECT column_id AS "column_id",
               column_name AS "column_name",
               data_type AS "data_type",
               data_length AS "data_length",
               data_precision AS "data_precision",
               data_scale AS "data_scale",
               nullable AS "nullable"
        FROM all_tab_columns
        WHERE owner = :owner AND table_name = :name
        ORDER BY column_id
        """,
        {"owner": owner, "name": name},
        notices,
        "Columns",
    )
    comments = _optional(
        connection,
        """
        SELECT comments AS "comments"
        FROM all_tab_comments
        WHERE owner = :owner AND table_name = :name
        """,
        {"owner": owner, "name": name},
        notices,
        "Comments",
    )
    stats = []
    if object_type == "TABLE":
        stats = _optional(
            connection,
            """
            SELECT tablespace_name AS "tablespace_name",
                   num_rows AS "num_rows",
                   TO_CHAR(last_analyzed, 'YYYY-MM-DD HH24:MI:SS') AS "last_analyzed",
                   partitioned AS "partitioned"
            FROM all_tables
            WHERE owner = :owner AND table_name = :name
            """,
            {"owner": owner, "name": name},
            notices,
            "Statistics",
        )
    constraints = _optional(
        connection,
        """
        SELECT cons.constraint_name AS "constraint_name",
               cons.constraint_type AS "constraint_type",
               cols.column_name AS "column_name",
               cols.position AS "position"
        FROM all_constraints cons
        JOIN all_cons_columns cols
          ON cols.owner = cons.owner
         AND cols.constraint_name = cons.constraint_name
        WHERE cons.owner = :owner
          AND cons.table_name = :name
          AND cons.constraint_type IN ('P', 'U', 'R')
        ORDER BY cons.constraint_type, cons.constraint_name, cols.position
        """,
        {"owner": owner, "name": name},
        notices,
        "Constraints",
    )
    indexes = _optional(
        connection,
        """
        SELECT index_name AS "index_name",
               uniqueness AS "uniqueness",
               status AS "status"
        FROM all_indexes
        WHERE table_owner = :owner AND table_name = :name
        ORDER BY index_name
        """,
        {"owner": owner, "name": name},
        notices,
        "Indexes",
    )
    preview, preview_columns, truncated = _preview(connection, owner, name, notices)
    return {
        "kind": "table",
        "owner": owner,
        "name": name,
        "object_type": object_type,
        "comment": comments[0]["comments"] if comments else None,
        "stats": stats[0] if stats else None,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "preview_columns": preview_columns,
        "preview_rows": preview,
        "preview_truncated": truncated,
        "notice": " ".join(notices) or None,
    }


def _describe_source(connection: oracledb.Connection, owner: str, name: str, object_type: str) -> dict:
    try:
        rows = fetch_dicts(
            connection,
            """
            SELECT text AS "text"
            FROM all_source
            WHERE owner = :owner AND name = :name AND type = :object_type
            ORDER BY line
            """,
            {"owner": owner, "name": name, "object_type": object_type},
        )
    except QueryFailed as exc:
        raise AppError(first_line(exc.message), 400) from exc
    source = "".join(str(row.get("text") or "") for row in rows)
    notice = None
    if not source:
        notice = (
            "No source is visible for this object. "
            "It may be wrapped, or this user cannot read ALL_SOURCE for it."
        )
    return {
        "kind": "source",
        "owner": owner,
        "name": name,
        "object_type": object_type,
        "source": source[:200_000],
        "truncated": len(source) > 200_000,
        "notice": notice,
    }


def browse_rows(
    connection: oracledb.Connection,
    owner: str,
    name: str,
    where: str,
    column: str,
    op: str,
    value: str,
    limit: int,
) -> dict:
    owner = _ident(owner, "owner")
    name = _ident(name, "object")
    clause, binds = _row_predicate(where, column, op, value)
    binds["lim"] = int(limit)
    sql = f'SELECT * FROM "{owner}"."{name}" WHERE {clause} AND ROWNUM <= :lim'
    cursor = connection.cursor()
    try:
        cursor.execute(sql, binds)
        columns = [item[0] for item in cursor.description] if cursor.description else []
        raw_rows = cursor.fetchmany(limit + 1)
    except oracledb.Error as exc:
        raise AppError(
            "The data query failed. Check the WHERE clause and the column filter. "
            + first_line(str(exc))
        ) from exc
    finally:
        cursor.close()
    truncated = len(raw_rows) > limit
    rows = [[jsonable(item) for item in row] for row in raw_rows[:limit]]
    return {
        "owner": owner,
        "name": name,
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "limit": limit,
        "filter": clause,
    }


def _row_predicate(where: str, column: str, op: str, value: str) -> tuple[str, dict]:
    parts: list[str] = []
    binds: dict = {}
    text = (where or "").strip()
    if text:
        if len(text) > 2000 or ";" in text or "\x00" in text:
            raise AppError(
                "The WHERE clause was not sent to Oracle. "
                "Type only the condition, without the word WHERE, with no semicolon, "
                "and keep it under 2000 characters."
            )
        parts.append("(" + text + ")")
    if op:
        quoted = f'"{_ident(column, "column")}"'
        if op == "null":
            parts.append(f"{quoted} IS NULL")
        elif op == "notnull":
            parts.append(f"{quoted} IS NOT NULL")
        elif op == "eq":
            binds["filt"] = value
            parts.append(f"{quoted} = :filt")
        elif op == "ne":
            binds["filt"] = value
            parts.append(f"{quoted} <> :filt")
        elif op in {"contains", "starts"}:
            binds["filt"] = _like_value(value, op)
            parts.append(f"UPPER({quoted}) LIKE :filt ESCAPE '\\'")
        else:
            raise AppError("That column filter is not supported.")
    elif column:
        raise AppError("Choose a column filter operator, or clear the column.")
    return " AND ".join(parts) if parts else "1 = 1", binds


def _like_value(value: str, op: str) -> str:
    if len(value) > 200:
        raise AppError("The column filter value is too long.")
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if op == "starts":
        return escaped.upper() + "%"
    return "%" + escaped.upper() + "%"


def _preview(connection: oracledb.Connection, owner: str, name: str, notices: list[str]):
    sql = f'SELECT * FROM "{owner}"."{name}" WHERE ROWNUM <= 100'
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        columns = [item[0] for item in cursor.description] if cursor.description else []
        raw_rows = cursor.fetchmany(101)
        truncated = len(raw_rows) > 100
        rows = [[jsonable(value) for value in row] for row in raw_rows[:100]]
        return rows, columns, truncated
    except oracledb.Error as exc:
        notices.append(f"Data preview failed ({first_line(str(exc))}).")
        return [], [], False
    finally:
        cursor.close()


def _optional(connection, sql: str, binds: dict, notices: list[str], label: str) -> list[dict]:
    try:
        return fetch_dicts(connection, sql, binds)
    except QueryFailed as exc:
        if is_missing_privilege(exc.message):
            notices.append(f"{label} could not be read ({first_line(exc.message)}).")
            return []
        raise AppError(first_line(exc.message), 400) from exc


def _ident(value: str, label: str) -> str:
    if not _IDENT.fullmatch(value or ""):
        raise AppError(
            f"The {label} contains characters this browser will not put into SQL. "
            "Quoted names with punctuation are skipped."
        )
    return value


def _like_prefix(query: str) -> str | None:
    text = (query or "").strip().upper()
    if not text:
        return None
    if len(text) > 128:
        raise AppError("The object filter is too long.")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"
