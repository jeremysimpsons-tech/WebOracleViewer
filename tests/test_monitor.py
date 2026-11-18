import pytest

from app.errors import AppError, QueryFailed
from app.monitor import build_sessions, build_tablespaces, kill_session_sql


def test_sessions_fall_back_and_keep_the_oracle_error():
    def fetch(sql: str):
        if "v$session" in sql.lower():
            raise QueryFailed("ORA-00942: table or view does not exist")
        if "SYS_CONTEXT" in sql:
            return [{"sid": 12, "username": "HR", "status": "ACTIVE", "blocking_session": None}]
        raise AssertionError(sql)

    report = build_sessions(fetch)
    assert report["source"] == "session_context"
    assert "ORA-00942" in report["notice"]
    assert "V_$SESSION" in report["notice"]
    assert report["rows"][0]["username"] == "HR"
    assert report["summary"]["active"] == 1


def test_sql_text_failure_keeps_session_client_and_wait_columns():
    def fetch(sql: str):
        lowered = sql.lower()
        if "v$sql" in lowered:
            raise QueryFailed("ORA-00942: table or view does not exist")
        if "from v$session" in lowered and "module" in lowered:
            return [{
                "sid": 8,
                "serial": 4,
                "username": "HR",
                "status": "INACTIVE",
                "blocking_session": 3,
                "machine": "laptop",
                "program": "sqlplus",
                "sql_id": "abc",
            }]
        if "sys_context" in lowered:
            return [{"sid": 8}]
        raise AssertionError(sql)

    report = build_sessions(fetch)
    assert report["source"] == "v$session"
    assert report["rows"][0]["program"] == "sqlplus"
    assert "V_$SQL" in report["notice"]
    assert report["current_sid"] == 8
    assert report["current_serial"] == 4
    assert report["summary"]["blocked"] == 1


def test_kill_session_statement_uses_integers_only():
    assert kill_session_sql(42, 7) == "ALTER SYSTEM KILL SESSION '42,7' IMMEDIATE"
    with pytest.raises(AppError):
        kill_session_sql(True, 1)  # type: ignore[arg-type]
    with pytest.raises(AppError):
        kill_session_sql(0, 1)


def test_tablespaces_fall_back_to_the_user_views():
    def fetch(sql: str):
        lowered = sql.lower()
        if "dba_" in lowered:
            raise QueryFailed("ORA-01031: insufficient privileges")
        if "user_tablespaces" in lowered:
            return [{"tablespace_name": "USERS", "contents": "PERMANENT", "status": "ONLINE"}]
        if "user_segments" in lowered:
            return [{"tablespace_name": "USERS", "used_mb": 10}]
        if "user_free_space" in lowered:
            return [{"tablespace_name": "USERS", "free_mb": 40}]
        raise AssertionError(sql)

    report = build_tablespaces(fetch)
    assert report["source"] == "user_tablespaces"
    assert "ORA-01031" in report["notice"]
    assert "not the whole instance" in report["notice"]
    assert report["rows"][0]["pct_used"] == 20.0
    assert report["summary"]["count"] == 1


def test_tablespaces_use_dba_views_when_they_are_granted():
    def fetch(sql: str):
        lowered = sql.lower()
        if "dba_temp_files" in lowered:
            return []
        if "dba_tablespaces" in lowered:
            return [{
                "tablespace_name": "SYSTEM",
                "contents": "PERMANENT",
                "status": "ONLINE",
                "total_mb": 100,
                "used_mb": 96,
                "free_mb": 4,
                "pct_used": 96,
                "max_mb": 200,
                "files": 1,
            }]
        raise AssertionError(sql)

    report = build_tablespaces(fetch)
    assert report["source"] == "dba_tablespaces"
    assert report["notice"] is None
    assert report["summary"]["critical"] == 1
