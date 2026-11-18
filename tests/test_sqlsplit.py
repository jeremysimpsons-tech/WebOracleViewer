import pytest

from app.errors import AppError
from app.sqlsplit import prepare_for_driver, split_script, statement_kind
from app.worksheet import ensure_allowed


def _sql(script: str) -> list[str]:
    return [item.text for item in split_script(script) if item.kind == "sql"]


def test_two_selects_and_quoted_semicolon():
    assert _sql("SELECT 1 FROM dual; SELECT 2 FROM dual;") == [
        "SELECT 1 FROM dual;",
        "SELECT 2 FROM dual;",
    ]
    assert _sql("SELECT 'a;b' FROM dual; SELECT q'[a;b]' FROM dual;") == [
        "SELECT 'a;b' FROM dual;",
        "SELECT q'[a;b]' FROM dual;",
    ]
    assert prepare_for_driver("SELECT 1 FROM dual;") == "SELECT 1 FROM dual"


def test_plsql_keeps_internal_semicolons():
    block = "BEGIN\n  IF 1=1 THEN\n    NULL;\n  END IF;\nEND;"
    parts = _sql(block)
    assert len(parts) == 1
    assert "END IF;" in parts[0]
    assert prepare_for_driver(parts[0]).endswith("END;")
    declare = "DECLARE\n  x NUMBER := 1;\nBEGIN\n  NULL;\nEND;"
    assert len(_sql(declare)) == 1
    procedure = "CREATE OR REPLACE PROCEDURE p AS\nBEGIN\n  NULL;\nEND;"
    assert len(_sql(procedure)) == 1
    assert statement_kind(procedure) == "plsql"


def test_slash_separates_statements():
    script = "SELECT 1 FROM dual;\n/\nBEGIN\n  NULL;\nEND;\n/\n"
    parts = _sql(script)
    assert len(parts) == 2
    assert parts[0].startswith("SELECT 1")
    assert parts[1].startswith("BEGIN")


def test_directives_and_real_set_statements():
    items = split_script("SET SERVEROUTPUT ON\nPROMPT hello\nSET ROLE app_role;\n")
    assert items[0].kind == "serveroutput" and items[0].enabled is True
    assert items[1].kind == "prompt" and items[1].text == "hello"
    assert items[2].kind == "sql"
    assert prepare_for_driver(items[2].text) == "SET ROLE app_role"
    unsupported = split_script("SET FEEDBACK ON")
    assert unsupported[0].kind == "unsupported"


def test_comments_do_not_become_statements():
    assert _sql("-- comment\nSELECT 1 FROM dual;") == ["SELECT 1 FROM dual;"]


def test_read_only_blocks_plsql_and_dml():
    ensure_allowed(split_script("SELECT 1 FROM dual"), True)
    with pytest.raises(AppError):
        ensure_allowed(split_script("BEGIN NULL; END;"), True)
    with pytest.raises(AppError):
        ensure_allowed(split_script("UPDATE employees SET name = 'x'"), True)
