import time
from types import SimpleNamespace

import oracledb
import pytest

from app.db import PAGE_CURSOR_IDLE_SECONDS, SessionRegistry, TRANSACTION_IDLE_SECONDS
from app.errors import AppError, QueryFailed
from app.monitor import build_session_sql
from app.prefs import ClientPrefs
from app.sqlsplit import is_locking_sql, needs_ddl_confirm
from app.worksheet import execute_script


def test_locking_and_ddl_detection_ignores_strings_and_comments():
    assert is_locking_sql("select * from t for update")
    assert is_locking_sql("lock table t in row share mode")
    assert not is_locking_sql("select * from t where note = 'for update'")
    assert not is_locking_sql("select * from t -- for update\nwhere id = 1")
    assert needs_ddl_confirm("drop table t")
    assert needs_ddl_confirm("alter system kill session '1,2'")
    assert needs_ddl_confirm("grant select on t to hr")
    assert not needs_ddl_confirm("alter session set nls_date_format = 'YYYY'")
    assert not needs_ddl_confirm("select * from t")


def test_read_only_refuses_locking_sql_when_the_setting_is_on():
    handle = SimpleNamespace(conn=SimpleNamespace(), read_only=True, page_cursors={})
    with pytest.raises(AppError, match="locks rows"):
        execute_script(
            handle,
            "select * from t for update",
            10,
            False,
            None,
            [],
            block_locking_sql=True,
        )


def test_ddl_requires_confirmation():
    handle = SimpleNamespace(conn=SimpleNamespace(), read_only=False, page_cursors={})
    with pytest.raises(AppError) as caught:
        execute_script(handle, "drop table t", 10, False, None, [], confirm_ddl=False)
    assert caught.value.extra["code"] == "confirm_ddl"


def test_a_failed_script_rolls_back_when_asked():
    class Cursor:
        description = None
        rowcount = 1

        def prepare(self, sql):
            self.sql = sql

        def bindnames(self):
            return []

        def execute(self, sql, _params=None):
            if "FAIL" in sql.upper():
                raise oracledb.DatabaseError("ORA-00942: table or view does not exist")

        def callproc(self, *_args):
            raise oracledb.DatabaseError("ORA-06550")

        def arrayvar(self, *_args):
            raise oracledb.DatabaseError("ORA-06550")

        def var(self, *_args):
            raise oracledb.DatabaseError("ORA-06550")

        def close(self):
            return None

    connection = SimpleNamespace(autocommit=False, rolled=False, cursor=lambda: Cursor())

    def rollback():
        connection.rolled = True

    connection.rollback = rollback
    handle = SimpleNamespace(conn=connection, read_only=False, page_cursors={}, open_transaction=False)
    result = execute_script(
        handle,
        "update t set a = 1;\nupdate FAIL set a = 1",
        10,
        False,
        None,
        [],
        confirm_ddl=True,
        rollback_script_on_error=True,
    )
    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert connection.rolled is True


def test_cancel_does_not_roll_the_script_back():
    class Cursor:
        description = None
        rowcount = 1

        def prepare(self, sql):
            self.sql = sql

        def bindnames(self):
            return []

        def execute(self, _sql, _params=None):
            raise oracledb.DatabaseError("ORA-01013: user requested cancel of current operation")

        def callproc(self, *_args):
            raise oracledb.DatabaseError("ORA-06550")

        def arrayvar(self, *_args):
            raise oracledb.DatabaseError("ORA-06550")

        def var(self, *_args):
            raise oracledb.DatabaseError("ORA-06550")

        def close(self):
            return None

    connection = SimpleNamespace(autocommit=False, rolled=False, cursor=lambda: Cursor(), rollback=lambda: None)
    connection.rollback = lambda: setattr(connection, "rolled", True)
    handle = SimpleNamespace(conn=connection, read_only=False, page_cursors={}, open_transaction=True)
    result = execute_script(handle, "update t set a = 1", 10, False, None, [], rollback_script_on_error=True)
    assert result["rolled_back"] is False
    assert connection.rolled is False
    assert "cancelled" in result["error"]["message"].lower()


def test_idle_transaction_rolls_back_without_closing_the_session():
    connection = SimpleNamespace(transaction_in_progress=True, rolled=False, closed=False)

    def rollback():
        connection.rolled = True
        connection.transaction_in_progress = False

    connection.rollback = rollback
    connection.close = lambda: setattr(connection, "closed", True)
    handle = SimpleNamespace(
        sid="browser",
        connection_id="one",
        conn=connection,
        lock=__import__("threading").Lock(),
        opened_at=0,
        last_used=time.time() - TRANSACTION_IDLE_SECONDS - 5,
        read_only=False,
        name="Lab",
        db_user="HR",
        db_name="FREE",
        service_name="freepdb1",
        page_cursors={},
        idle_rollback_note="",
        open_transaction=True,
    )
    registry = SessionRegistry(SimpleNamespace(db_session_idle_seconds=1800))
    registry._handles[("browser", "one")] = handle
    registry._active["browser"] = "one"
    registry.reap_idle()
    assert connection.rolled is True
    assert connection.closed is False
    assert registry.get("browser") is handle
    assert "5 minutes" in registry.take_idle_note("browser")


def test_unused_page_cursors_close_after_two_minutes():
    cursor = SimpleNamespace(closed=False, close=lambda: None)

    def close():
        cursor.closed = True

    cursor.close = close
    connection = SimpleNamespace(transaction_in_progress=False, rollback=lambda: None, close=lambda: None)
    handle = SimpleNamespace(
        sid="browser",
        connection_id="one",
        conn=connection,
        lock=__import__("threading").Lock(),
        opened_at=0,
        last_used=time.time(),
        read_only=False,
        name="Lab",
        db_user="HR",
        db_name="FREE",
        service_name="freepdb1",
        page_cursors={"tok": {"cursor": cursor, "touched": time.time() - PAGE_CURSOR_IDLE_SECONDS - 5}},
        idle_rollback_note="",
        open_transaction=False,
    )
    registry = SessionRegistry(SimpleNamespace(db_session_idle_seconds=1800))
    registry._handles[("browser", "one")] = handle
    registry._active["browser"] = "one"
    registry.reap_idle()
    assert cursor.closed is True
    assert handle.page_cursors == {}
    assert registry.get("browser") is handle


def test_cancel_does_not_need_the_statement_lock():
    connection = SimpleNamespace(cancelled=False, cancel=lambda: None)

    def cancel():
        connection.cancelled = True

    connection.cancel = cancel
    handle = SimpleNamespace(
        sid="browser",
        connection_id="one",
        conn=connection,
        lock=__import__("threading").Lock(),
        name="Lab",
    )
    assert handle.lock.acquire(blocking=False)
    registry = SessionRegistry(SimpleNamespace(db_session_idle_seconds=1800))
    registry._handles[("browser", "one")] = handle
    registry._active["browser"] = "one"
    registry.cancel("browser")
    assert connection.cancelled is True
    handle.lock.release()


def test_open_connection_limit():
    handle = SimpleNamespace(connection_id="one")
    registry = SessionRegistry(SimpleNamespace(db_session_idle_seconds=1800))
    registry._handles[("browser", "one")] = handle
    with pytest.raises(AppError, match="limit is 1"):
        registry.connect("browser", {"id": "two"}, {}, False, max_open=1)


def test_client_prefs_round_trip(tmp_path):
    prefs = ClientPrefs(SimpleNamespace(data_dir=tmp_path))
    assert prefs.get()["max_open_connections"] == 5
    saved = prefs.update({
        "block_locking_sql": False,
        "rollback_script_on_error": False,
        "max_open_connections": 8,
        "tag_sessions": False,
    })
    assert saved["max_open_connections"] == 8
    again = ClientPrefs(SimpleNamespace(data_dir=tmp_path))
    assert again.get()["block_locking_sql"] is False
    assert (tmp_path / "client-prefs.json").stat().st_mode & 0o777 == 0o600


def test_session_sql_is_one_session_and_falls_back_without_sql_text():
    def fetch(sql, binds):
        assert binds == {"sid": 4, "serial": 9}
        if "v$sql" in sql.lower():
            raise QueryFailed("ORA-00942: table or view does not exist")
        return [{"sql_id": "abc", "prev_sql_id": "def"}]

    report = build_session_sql(fetch, 4, 9)
    assert report["sql_id"] == "abc"
    assert "V_$SQL" in report["notice"]
