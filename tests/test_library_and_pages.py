from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.library import QueryLibrary
from app.worksheet import _run_one, fetch_next_page


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.description = [("ID",)]
        self.arraysize = 0
        self.closed = False

    def prepare(self, _sql):
        return None

    def bindnames(self):
        return []

    def execute(self, _sql, _params=None):
        return None

    def fetchmany(self, count):
        chunk = self.rows[:count]
        del self.rows[:count]
        return chunk

    def close(self):
        self.closed = True


def _query(rows, page_size):
    cursor = _Cursor(rows)
    handle = SimpleNamespace(conn=SimpleNamespace(cursor=lambda: cursor), page_cursors={})
    result = _run_one(handle, 1, "select * from table1", page_size, {})
    return handle, cursor, result


def test_a_large_select_returns_one_page_and_keeps_the_cursor():
    handle, cursor, result = _query([(n,) for n in range(120)], 50)
    assert result["row_count"] == 50
    assert result["rows"][0] == [0]
    assert result["rows"][-1] == [49]
    assert result["has_more"] is True
    assert cursor.closed is False
    assert len(cursor.rows) == 70

    second = fetch_next_page(handle, result["page_token"])
    assert second["row_start"] == 51
    assert second["rows"][0] == [50]
    assert second["rows"][-1] == [99]
    assert second["has_more"] is True

    third = fetch_next_page(handle, second["page_token"])
    assert third["row_count"] == 20
    assert third["rows"][0] == [100]
    assert third["has_more"] is False
    assert third["page_token"] is None
    assert cursor.closed is True
    assert cursor.rows == []


def test_exact_page_offers_one_more_fetch_without_skipping_rows():
    handle, cursor, result = _query([(1,), (2,)], 2)
    assert [row[0] for row in result["rows"]] == [1, 2]
    assert result["has_more"] is True
    nxt = fetch_next_page(handle, result["page_token"])
    assert nxt["rows"] == []
    assert nxt["has_more"] is False
    assert cursor.closed is True


def test_missing_page_is_a_clear_error():
    handle = SimpleNamespace(page_cursors={})
    with pytest.raises(AppError, match="no longer open"):
        fetch_next_page(handle, "missing-token")


def test_query_buckets_can_be_added_renamed_and_deleted(tmp_path):
    library = QueryLibrary(SimpleNamespace(data_dir=tmp_path))
    admin = library.create_bucket("Admin queries")
    weekly = library.create_bucket("Weekly report")
    saved = library.save_query(admin["id"], "Long sessions", "select * from v$session")
    library.rename_bucket(weekly["id"], "Business Search")
    names = [bucket["name"] for bucket in library.list_buckets()]
    assert names == ["Admin queries", "Business Search"]
    loaded = library.list_buckets()[0]["queries"][0]
    assert loaded["sql"] == "select * from v$session"
    library.update_query(saved["id"], "Long sessions", "select sid from v$session")
    library.delete_query(saved["id"])
    assert library.list_buckets()[0]["queries"] == []
    library.delete_bucket(admin["id"])
    assert [bucket["name"] for bucket in library.list_buckets()] == ["Business Search"]
    with pytest.raises(AppError, match="already exists"):
        library.create_bucket("business search")
    assert (tmp_path / "query-library.json").stat().st_mode & 0o777 == 0o600
