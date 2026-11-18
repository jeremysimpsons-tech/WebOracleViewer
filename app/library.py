"""Named buckets of worksheet SQL. The text is stored for the operator, not executed here."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.errors import AppError

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:#()/\-]{0,79}")
_MAX_BUCKETS = 40
_MAX_QUERIES = 100
_MAX_SQL = 100_000


class QueryLibrary:
    def __init__(self, settings: Settings) -> None:
        self.path: Path = settings.data_dir / "query-library.json"
        self._lock = threading.Lock()

    def list_buckets(self) -> list[dict]:
        with self._lock:
            buckets = self._read()["buckets"]
        buckets.sort(key=lambda item: item["name"].lower())
        for bucket in buckets:
            bucket["queries"].sort(key=lambda item: item["title"].lower())
        return buckets

    def create_bucket(self, name: str) -> dict:
        clean = _title(name, "bucket")
        with self._lock:
            data = self._read()
            if len(data["buckets"]) >= _MAX_BUCKETS:
                raise AppError(f"The library is limited to {_MAX_BUCKETS} buckets.")
            _unique_bucket(data["buckets"], clean, None)
            bucket = {"id": str(uuid.uuid4()), "name": clean, "queries": []}
            data["buckets"].append(bucket)
            self._write(data)
        return bucket

    def rename_bucket(self, bucket_id: str, name: str) -> dict:
        clean = _title(name, "bucket")
        with self._lock:
            data = self._read()
            bucket = _bucket(data, bucket_id)
            _unique_bucket(data["buckets"], clean, bucket_id)
            bucket["name"] = clean
            self._write(data)
            return _copy_bucket(bucket)

    def delete_bucket(self, bucket_id: str) -> None:
        with self._lock:
            data = self._read()
            before = len(data["buckets"])
            data["buckets"] = [item for item in data["buckets"] if item["id"] != bucket_id]
            if len(data["buckets"]) == before:
                raise AppError("That query bucket was not found.", 404)
            self._write(data)

    def save_query(self, bucket_id: str, title: str, sql: str) -> dict:
        clean_title = _title(title, "query")
        text = _sql(sql)
        with self._lock:
            data = self._read()
            bucket = _bucket(data, bucket_id)
            if len(bucket["queries"]) >= _MAX_QUERIES:
                raise AppError(f"A bucket is limited to {_MAX_QUERIES} queries.")
            _unique_query(bucket["queries"], clean_title, None)
            query = {
                "id": str(uuid.uuid4()),
                "title": clean_title,
                "sql": text,
                "updated_at": _now(),
            }
            bucket["queries"].append(query)
            self._write(data)
        return query

    def update_query(self, query_id: str, title: str, sql: str) -> dict:
        clean_title = _title(title, "query")
        text = _sql(sql)
        with self._lock:
            data = self._read()
            bucket, query = _query(data, query_id)
            _unique_query(bucket["queries"], clean_title, query_id)
            query["title"] = clean_title
            query["sql"] = text
            query["updated_at"] = _now()
            self._write(data)
            return dict(query)

    def delete_query(self, query_id: str) -> None:
        with self._lock:
            data = self._read()
            for bucket in data["buckets"]:
                before = len(bucket["queries"])
                bucket["queries"] = [item for item in bucket["queries"] if item["id"] != query_id]
                if len(bucket["queries"]) != before:
                    self._write(data)
                    return
        raise AppError("That saved query was not found.", 404)

    def _read(self) -> dict:
        if not self.path.is_file():
            return {"buckets": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError("The query library could not be read.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("buckets"), list):
            raise AppError("The query library file is not valid.")
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def _title(value: str, label: str) -> str:
    text = " ".join(str(value or "").split())
    if not _NAME.fullmatch(text):
        raise AppError(
            f"The {label} name must start with a letter or digit and stay under 80 characters. "
            "Letters, digits, spaces, and . _ : # ( ) / - are allowed."
        )
    return text


def _sql(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AppError("The worksheet is empty. Write the SQL before saving it.")
    if len(text) > _MAX_SQL:
        raise AppError("That query is too long to save.")
    if "\x00" in text:
        raise AppError("The query contains a character that cannot be saved.")
    return text


def _bucket(data: dict, bucket_id: str) -> dict:
    for bucket in data["buckets"]:
        if bucket["id"] == bucket_id:
            return bucket
    raise AppError("That query bucket was not found.", 404)


def _query(data: dict, query_id: str) -> tuple[dict, dict]:
    for bucket in data["buckets"]:
        for query in bucket["queries"]:
            if query["id"] == query_id:
                return bucket, query
    raise AppError("That saved query was not found.", 404)


def _unique_bucket(buckets: list[dict], name: str, ignore_id: str | None) -> None:
    folded = name.casefold()
    for bucket in buckets:
        if bucket["id"] != ignore_id and bucket["name"].casefold() == folded:
            raise AppError("A bucket with that name already exists.", 409)


def _unique_query(queries: list[dict], title: str, ignore_id: str | None) -> None:
    folded = title.casefold()
    for query in queries:
        if query["id"] != ignore_id and query["title"].casefold() == folded:
            raise AppError("That bucket already has a query with this title.", 409)


def _copy_bucket(bucket: dict) -> dict:
    return {"id": bucket["id"], "name": bucket["name"], "queries": list(bucket["queries"])}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
