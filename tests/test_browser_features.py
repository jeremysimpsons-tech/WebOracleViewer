from app.catalog import browse_rows
from app.db import DbHandle, SessionRegistry
from app.errors import AppError, QueryFailed
from app.monitor import build_runtime


class _Conn:
    transaction_in_progress = False

    def rollback(self):
        return None

    def close(self):
        return None


def _handle(sid: str, connection_id: str, name: str) -> DbHandle:
    return DbHandle(
        sid=sid,
        connection_id=connection_id,
        conn=_Conn(),
        lock=__import__("threading").Lock(),
        opened_at=0,
        last_used=0,
        read_only=False,
        name=name,
        db_user="SYSTEM",
        db_name="FREE",
        service_name="freepdb1",
    )


def test_several_connections_stay_open_and_one_is_working():
    registry = SessionRegistry(settings=None)
    registry._handles[("browser", "one")] = _handle("browser", "one", "Lab")
    registry._handles[("browser", "two")] = _handle("browser", "two", "Prod")
    registry._active["browser"] = "one"

    assert registry.get("browser").name == "Lab"
    registry.activate("browser", "two")
    assert registry.get("browser").name == "Prod"
    assert [item["name"] for item in registry.open_sessions("browser")] == ["Lab", "Prod"]

    registry.disconnect("browser", confirm=True, connection_id="two")
    assert registry.get("browser").name == "Lab"
    assert len(registry.open_sessions("browser")) == 1


class _Cursor:
    def __init__(self):
        self.sql = ""
        self.binds = {}
        self.description = [("DEPTNO",)]

    def execute(self, sql, binds=None):
        self.sql = sql
        self.binds = binds or {}

    def fetchmany(self, _count):
        return [(10,)]

    def close(self):
        return None


class _Database:
    def __init__(self):
        self.cursor_used = _Cursor()

    def cursor(self):
        return self.cursor_used


def test_table_filter_keeps_the_where_clause_and_binds_the_column():
    database = _Database()
    report = browse_rows(database, "HR", "EMP", "deptno = 10", "ENAME", "contains", "ki", 25)
    assert report["filter"].startswith("(deptno = 10)")
    assert 'UPPER("ENAME") LIKE :filt' in database.cursor_used.sql
    assert database.cursor_used.binds["filt"] == "%KI%"
    assert database.cursor_used.binds["lim"] == 25
    assert report["rows"] == [[10]]


def test_where_clause_with_a_semicolon_is_refused():
    try:
        browse_rows(_Database(), "HR", "EMP", "1=1; drop table emp", "", "", "", 10)
    except AppError as exc:
        assert "semicolon" in exc.message
    else:
        raise AssertionError("expected the WHERE clause to be refused")


def test_runtime_section_names_the_missing_view():
    def fetch(sql: str):
        if "v$instance" in sql.lower():
            raise QueryFailed("ORA-01031: insufficient privileges")
        if "SYS_CONTEXT" in sql:
            return [{"instance_name": "FREE", "session_user": "SYSTEM"}]
        if "v$sga" in sql.lower():
            return [{"name": "Database Buffers", "mb": 128}]
        raise QueryFailed("ORA-00942: table or view does not exist")

    report = build_runtime(fetch)
    instance = report["sections"][0]
    assert instance["id"] == "instance"
    assert "V_$INSTANCE" in instance["notice"]
    assert instance["rows"][0]["session_user"] == "SYSTEM"
    memory = next(section for section in report["sections"] if section["id"] == "memory")
    assert memory["notice"] is None
    assert memory["rows"][0]["name"] == "Database Buffers"
    top = next(section for section in report["sections"] if section["id"] == "top-sql")
    assert "V_$SQL" in top["notice"]
