"""Split a worksheet script the way a SQL*Plus buffer does.

Statements end at a semicolon. Anonymous PL/SQL and CREATE statements for
procedures, functions, packages, triggers, and type bodies keep their internal
semicolons until the outer ``END;`` or until a line that contains only ``/``.
A line that contains only ``/`` always ends the current statement and is not
sent to Oracle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PLSQL_CREATE = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?"
    r"(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE\s+BODY)\b",
    re.IGNORECASE,
)
_DDL = {
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "COMMENT",
    "RENAME",
    "ANALYZE",
    "FLASHBACK",
    "PURGE",
    "AUDIT",
    "NOAUDIT",
}
_DML = {"INSERT", "UPDATE", "DELETE", "MERGE", "LOCK"}
_OPENERS = {"[", "{", "<", "("}
_CLOSERS = {"]": "[", "}": "{", ">": "<", ")": "("}


@dataclass(frozen=True)
class ScriptItem:
    kind: str
    text: str
    enabled: bool = True


def split_script(script: str) -> list[ScriptItem]:
    if script.startswith("\ufeff"):
        script = script[1:]
    script = script.replace("\r\n", "\n").replace("\r", "\n")
    items: list[ScriptItem] = []
    index = 0
    length = len(script)
    while index < length:
        index = _skip_ws_and_comments(script, index)
        if index >= length:
            break
        if _at_slash_line(script, index):
            index = _after_slash_line(script, index)
            continue
        directive = _match_directive(script, index)
        if directive is not None:
            item, index = directive
            if item is not None:
                items.append(item)
            continue
        if _is_plsql(script, index):
            end = _consume_plsql(script, index)
        else:
            end = _consume_sql(script, index)
        if end <= index:
            index += 1
            continue
        text = script[index:end].strip()
        if text:
            items.append(ScriptItem("sql", text))
        index = end
    return items


def statement_kind(sql: str) -> str:
    head = _leading_text(sql, 0).lstrip()
    while head.startswith("("):
        head = head[1:].lstrip()
    match = re.match(r"[A-Za-z]+", head)
    word = match.group(0).upper() if match else ""
    if word in {"SELECT", "WITH"}:
        return "query"
    if _is_plsql(sql, 0):
        return "plsql"
    if word in _DML:
        return "dml"
    if word in _DDL:
        return "ddl"
    if word == "CALL":
        return "call"
    return "other"


def prepare_for_driver(sql: str) -> str:
    """Drop a trailing semicolon from SQL. Keep it on PL/SQL, where it is syntax."""
    text = sql.strip()
    if statement_kind(text) == "plsql":
        return text
    if text.endswith(";"):
        text = text[:-1].rstrip()
    return text


def _is_plsql(sql: str, index: int) -> bool:
    head = _leading_text(sql, index).lstrip()
    if re.match(r"(DECLARE|BEGIN)\b", head, re.IGNORECASE):
        return True
    return _PLSQL_CREATE.match(head) is not None


def _leading_text(sql: str, index: int, limit: int = 280) -> str:
    chars: list[str] = []
    length = len(sql)
    while index < length and len(chars) < limit:
        if sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = length if newline < 0 else newline + 1
            chars.append(" ")
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            index = length if end < 0 else end + 2
            chars.append(" ")
            continue
        chars.append(sql[index])
        index += 1
    return "".join(chars)


def _match_directive(sql: str, index: int) -> tuple[ScriptItem | None, int] | None:
    remainder = sql[index:]
    matched = re.match(r"SET\s+SERVEROUTPUT\s+(ON|OFF)\b[^\n]*\n?", remainder, re.IGNORECASE)
    if matched:
        flag = matched.group(1).upper()
        return ScriptItem("serveroutput", flag, flag == "ON"), index + matched.end()
    matched = re.match(r"PROMPT\b[ \t]*([^\n]*)\n?", remainder, re.IGNORECASE)
    if matched:
        return ScriptItem("prompt", matched.group(1).strip()), index + matched.end()
    matched = re.match(r"(?:REM|REMARK)\b[^\n]*\n?", remainder, re.IGNORECASE)
    if matched:
        return None, index + matched.end()
    # SET ROLE, SET TRANSACTION, and SET CONSTRAINT are Oracle SQL, not SQL*Plus.
    matched = re.match(
        r"SET\s+(?:ECHO|FEEDBACK|HEADING|LINESIZE|PAGESIZE|PAGES|DEFINE|VERIFY|TIMING|"
        r"LONG|WRAP|TRIMSPOOL|TAB|TERMOUT|SQLBLANKLINES|APPINFO|COLSEP|NUMWIDTH|"
        r"PAUSE|SCAN|AUTOCOMMIT)\b[^\n]*",
        remainder,
        re.IGNORECASE,
    )
    if matched:
        return ScriptItem("unsupported", " ".join(matched.group(0).split())), index + matched.end()
    return None


def _consume_sql(sql: str, start: int) -> int:
    index = start
    length = len(sql)
    while index < length:
        if _at_slash_line(sql, index):
            return index
        jump = _skip_noise(sql, index)
        if jump is not None:
            index = jump
            continue
        if sql[index] == ";":
            return index + 1
        index += 1
    return length


def _consume_plsql(sql: str, start: int) -> int:
    index = start
    length = len(sql)
    depth = 0
    closed = False
    while index < length:
        if _at_slash_line(sql, index):
            return index
        jump = _skip_noise(sql, index)
        if jump is not None:
            index = jump
            continue
        if _is_word_char(sql[index]) and (index == start or not _is_word_char(sql[index - 1])):
            end = index + 1
            while end < length and _is_word_char(sql[end]):
                end += 1
            word = sql[index:end].upper()
            following = _peek_word(sql, end)
            if word in {"BEGIN", "LOOP", "CASE", "IF"}:
                depth += 1
                closed = False
            elif word == "END":
                if following in {"IF", "LOOP", "CASE"}:
                    end = _end_of_next_word(sql, end)
                depth = max(0, depth - 1)
                closed = depth == 0
            index = end
            continue
        if sql[index] == ";" and closed and depth == 0:
            return index + 1
        index += 1
    return length


def _skip_noise(sql: str, index: int) -> int | None:
    if sql.startswith("--", index):
        newline = sql.find("\n", index)
        return len(sql) if newline < 0 else newline + 1
    if sql.startswith("/*", index):
        end = sql.find("*/", index + 2)
        return len(sql) if end < 0 else end + 2
    quoted = _qquote_end(sql, index)
    if quoted is not None:
        return quoted
    if sql[index] in "nN" and index + 1 < len(sql) and sql[index + 1] == "'":
        return _skip_quote(sql, index + 1)
    if sql[index] == "'":
        return _skip_quote(sql, index)
    if sql[index] == '"':
        return _skip_double(sql, index)
    return None


def _skip_quote(sql: str, index: int) -> int:
    index += 1
    length = len(sql)
    while index < length:
        if sql[index] == "'":
            if index + 1 < length and sql[index + 1] == "'":
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _skip_double(sql: str, index: int) -> int:
    index += 1
    length = len(sql)
    while index < length:
        if sql[index] == '"':
            if index + 1 < length and sql[index + 1] == '"':
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _qquote_end(sql: str, index: int) -> int | None:
    length = len(sql)
    cursor = index
    if sql[cursor] in "nN" and cursor + 1 < length and sql[cursor + 1] in "qQ":
        cursor += 1
    if not (cursor < length and sql[cursor] in "qQ" and cursor + 1 < length and sql[cursor + 1] == "'"):
        return None
    if cursor + 2 >= length:
        return length
    opener = sql[cursor + 2]
    closer = {value: key for key, value in _CLOSERS.items()}.get(opener, opener)
    if opener not in _OPENERS and opener == closer:
        pass
    scan = cursor + 3
    while scan < length - 1:
        if sql[scan] == closer and sql[scan + 1] == "'":
            return scan + 2
        scan += 1
    return length


def _skip_ws_and_comments(sql: str, index: int) -> int:
    length = len(sql)
    while index < length:
        if sql[index] in " \t\r\n":
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        break
    return index


def _at_slash_line(sql: str, index: int) -> bool:
    if index > 0 and sql[index - 1] != "\n":
        return False
    cursor = index
    length = len(sql)
    while cursor < length and sql[cursor] in " \t":
        cursor += 1
    if cursor >= length or sql[cursor] != "/":
        return False
    if cursor + 1 < length and sql[cursor + 1] == "*":
        return False
    cursor += 1
    while cursor < length and sql[cursor] in " \t":
        cursor += 1
    return cursor == length or sql[cursor] == "\n"


def _after_slash_line(sql: str, index: int) -> int:
    length = len(sql)
    cursor = index
    while cursor < length and sql[cursor] != "\n":
        cursor += 1
    if cursor < length and sql[cursor] == "\n":
        cursor += 1
    return cursor


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char in "_$#"


def _peek_word(sql: str, index: int) -> str:
    cursor = _skip_ws_and_comments(sql, index)
    if cursor >= len(sql) or not _is_word_char(sql[cursor]):
        return ""
    end = cursor + 1
    while end < len(sql) and _is_word_char(sql[end]):
        end += 1
    return sql[cursor:end].upper()


def _end_of_next_word(sql: str, index: int) -> int:
    cursor = _skip_ws_and_comments(sql, index)
    if cursor >= len(sql) or not _is_word_char(sql[cursor]):
        return index
    end = cursor + 1
    while end < len(sql) and _is_word_char(sql[end]):
        end += 1
    return end
