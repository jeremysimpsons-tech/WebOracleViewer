"""Open Oracle sessions and keep every connection a browser has opened."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import oracledb

from app.config import Settings
from app.errors import AppError, QueryFailed, first_line, redact
from app.jdbc import parse_connect_string

log = logging.getLogger("e1p.db")

_LOST = ("DPY-1001", "DPY-4011", "DPY-1002", "not connected", "closed the connection")


def connection_lost(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(code.lower() in text for code in _LOST)


def jsonable(value: object) -> object:
    if value is None or isinstance(value, str):
        if isinstance(value, str) and len(value) > 20_000:
            return value[:20_000] + "… (truncated)"
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        shown = value[:64].hex()
        return shown + ("…" if len(value) > 64 else "")
    if hasattr(value, "read"):
        data = value.read(20_000)
        return jsonable(data)
    return str(value)[:20_000]


def fetch_dicts(connection: oracledb.Connection, sql: str, binds: dict | None = None) -> list[dict]:
    cursor = connection.cursor()
    try:
        cursor.execute(sql, binds or {})
        if cursor.description is None:
            return []
        columns = [item[0].lower() for item in cursor.description]
        rows = []
        for raw in cursor:
            rows.append({columns[index]: jsonable(raw[index]) for index in range(len(columns))})
        return rows
    except oracledb.Error as exc:
        raise QueryFailed(str(exc)) from exc
    finally:
        cursor.close()


TRANSACTION_IDLE_SECONDS = 300
PAGE_CURSOR_IDLE_SECONDS = 120
EXPIRE_TIME_MINUTES = 2


def open_connection(
    record: dict,
    secrets: dict,
    settings: Settings,
    expire_time: int | None = None,
) -> oracledb.Connection:
    password = secrets.get("password") or ""
    wallet_password = secrets.get("wallet_password") or ""
    kwargs: dict = {
        "user": record["username"],
        "password": password,
        "tcp_connect_timeout": 15,
        "retry_count": 0,
        "program": "e1p-viewer",
    }
    if expire_time:
        kwargs["expire_time"] = int(expire_time)
    role = (record.get("role") or "").upper()
    if role == "SYSDBA":
        kwargs["mode"] = oracledb.AUTH_MODE_SYSDBA
    elif role == "SYSOPER":
        kwargs["mode"] = oracledb.AUTH_MODE_SYSOPER
    mode = record["mode"]
    try:
        if mode == "classic":
            kwargs.update(_classic_kwargs(record))
        elif mode == "wallet":
            kwargs.update(_wallet_kwargs(record, wallet_password))
        else:
            kwargs.update(_string_kwargs(record, wallet_password, settings))
        if kwargs.get("protocol") == "tcp":
            kwargs.pop("ssl_server_dn_match", None)
        connection = oracledb.connect(**kwargs)
    except oracledb.Error as exc:
        secret_values = [password, wallet_password]
        raise AppError(
            "Could not open the Oracle connection. " + redact(first_line(str(exc)), secret_values),
            400,
        ) from exc
    except AppError:
        raise
    except OSError as exc:
        log.warning("oracle connect failed name=%s error=%s", record.get("name"), type(exc).__name__)
        raise AppError(
            f"Could not open the Oracle connection to {record.get('host') or record.get('name')}: "
            f"{first_line(str(exc))}",
            400,
        ) from exc
    connection.call_timeout = settings.db_call_timeout_ms
    try:
        connection.module = "E1P DB Viewer"
    except oracledb.Error:
        pass
    log.info(
        "oracle connect name=%s user=%s mode=%s",
        record.get("name"),
        record.get("username"),
        mode,
    )
    return connection


def describe_session(connection: oracledb.Connection) -> dict:
    try:
        rows = fetch_dicts(
            connection,
            """
            SELECT SYS_CONTEXT('USERENV','SESSION_USER') AS "session_user",
                   SYS_CONTEXT('USERENV','DB_NAME') AS "db_name",
                   SYS_CONTEXT('USERENV','SERVICE_NAME') AS "service_name"
            FROM dual
            """,
        )
    except QueryFailed:
        return {"session_user": "", "db_name": "", "service_name": ""}
    if not rows:
        return {"session_user": "", "db_name": "", "service_name": ""}
    return rows[0]


def _classic_kwargs(record: dict) -> dict:
    kwargs = {
        "host": record["host"],
        "port": int(record["port"]),
        "protocol": record.get("protocol") or "tcp",
    }
    if record.get("sid"):
        kwargs["sid"] = record["sid"]
    else:
        kwargs["service_name"] = record["service_name"]
    if kwargs["protocol"] == "tcps":
        kwargs["ssl_server_dn_match"] = bool(record.get("ssl_server_dn_match", True))
    return kwargs


def _wallet_kwargs(record: dict, wallet_password: str) -> dict:
    wallet = record["wallet_dir"]
    kwargs = {
        "dsn": record["tns_alias"],
        "config_dir": wallet,
        "wallet_location": wallet,
        "ssl_server_dn_match": bool(record.get("ssl_server_dn_match", True)),
    }
    if wallet_password:
        kwargs["wallet_password"] = wallet_password
    return kwargs


def _string_kwargs(record: dict, wallet_password: str, settings: Settings) -> dict:
    parsed = parse_connect_string(record.get("connect_string") or "")
    if parsed.wallet_dir or record.get("wallet_dir"):
        wallet = record.get("wallet_dir") or parsed.wallet_dir
        kwargs = {
            "dsn": parsed.tns_alias or parsed.dsn,
            "config_dir": wallet,
            "wallet_location": wallet,
            "ssl_server_dn_match": bool(record.get("ssl_server_dn_match", True)),
        }
        if wallet_password:
            kwargs["wallet_password"] = wallet_password
        return kwargs
    if parsed.host and parsed.port and (parsed.service_name or parsed.sid):
        kwargs = {
            "host": parsed.host,
            "port": int(parsed.port),
            "protocol": parsed.protocol,
        }
        if parsed.sid:
            kwargs["sid"] = parsed.sid
        else:
            kwargs["service_name"] = parsed.service_name
        if parsed.protocol == "tcps":
            kwargs["ssl_server_dn_match"] = bool(record.get("ssl_server_dn_match", True))
        return kwargs
    kwargs = {"dsn": parsed.dsn}
    if parsed.protocol == "tcps":
        kwargs["ssl_server_dn_match"] = bool(record.get("ssl_server_dn_match", True))
    return kwargs


@dataclass
class DbHandle:
    sid: str
    connection_id: str
    conn: oracledb.Connection
    lock: threading.Lock
    opened_at: float
    last_used: float
    read_only: bool
    name: str
    db_user: str
    db_name: str
    service_name: str
    page_cursors: dict = field(default_factory=dict)
    idle_rollback_note: str = ""

    @property
    def open_transaction(self) -> bool:
        try:
            return bool(self.conn.transaction_in_progress)
        except oracledb.Error:
            return False

    def __repr__(self) -> str:
        return f"DbHandle(name={self.name!r}, user={self.db_user!r})"


class SessionRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._handles: dict[tuple[str, str], DbHandle] = {}
        self._active: dict[str, str] = {}
        self._sid_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="e1p-reaper", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self.close_all()

    def _loop(self) -> None:
        while not self._stop.wait(30):
            self.reap_idle()

    def reap_idle(self) -> None:
        now = time.time()
        session_deadline = now - self.settings.db_session_idle_seconds
        transaction_deadline = now - TRANSACTION_IDLE_SECONDS
        page_deadline = now - PAGE_CURSOR_IDLE_SECONDS
        closing: list[DbHandle] = []
        with self._lock:
            for key, handle in list(self._handles.items()):
                if not handle.lock.acquire(blocking=False):
                    continue
                if handle.last_used < session_deadline:
                    self._handles.pop(key, None)
                    if self._active.get(handle.sid) == handle.connection_id:
                        self._active.pop(handle.sid, None)
                    closing.append(handle)
                    continue
                self._maintain(handle, transaction_deadline, page_deadline)
                handle.lock.release()
        for handle in closing:
            try:
                self._close(handle, rollback=True)
            finally:
                handle.lock.release()

    def _maintain(self, handle: DbHandle, transaction_deadline: float, page_deadline: float) -> None:
        if handle.open_transaction and handle.last_used < transaction_deadline:
            try:
                handle.conn.rollback()
                handle.idle_rollback_note = (
                    "Uncommitted work was rolled back after 5 minutes with no activity. "
                    "The database session stayed open."
                )
            except oracledb.Error:
                log.warning("idle rollback failed name=%s", handle.name)
        stale = [
            token
            for token, slot in handle.page_cursors.items()
            if float(slot.get("touched") or 0) < page_deadline
        ]
        for token in stale:
            slot = handle.page_cursors.pop(token, None)
            if slot is None:
                continue
            try:
                slot["cursor"].close()
            except Exception:
                pass

    def cancel(self, sid: str) -> None:
        handle = self.get(sid)
        if handle is None:
            raise AppError(
                "Connect a database session first. Use Connect on a saved connection.",
                409,
            )
        try:
            handle.conn.cancel()
        except oracledb.Error as exc:
            raise AppError("The running statement could not be cancelled. " + first_line(str(exc))) from exc

    def take_idle_note(self, sid: str) -> str:
        handle = self.get(sid)
        if handle is None or not handle.idle_rollback_note:
            return ""
        note = handle.idle_rollback_note
        handle.idle_rollback_note = ""
        return note

    def get(self, sid: str) -> DbHandle | None:
        with self._lock:
            connection_id = self._active.get(sid)
            if not connection_id:
                return None
            return self._handles.get((sid, connection_id))

    def open_sessions(self, sid: str) -> list[dict]:
        with self._lock:
            handles = [handle for key, handle in self._handles.items() if key[0] == sid]
            active = self._active.get(sid)
        rows = []
        for handle in handles:
            rows.append(
                {
                    "connection_id": handle.connection_id,
                    "name": handle.name,
                    "db_user": handle.db_user,
                    "db_name": handle.db_name,
                    "service_name": handle.service_name,
                    "read_only": handle.read_only,
                    "open_transaction": handle.open_transaction,
                    "working": handle.connection_id == active,
                }
            )
        rows.sort(key=lambda item: item["name"].lower())
        return rows

    @contextmanager
    def session(self, sid: str, action: str | None = None):
        handle = self.get(sid)
        if handle is None:
            raise AppError(
                "Connect a database session first. Use Connect on a saved connection. "
                "The worksheet, object browser, sessions, tablespaces, and runtime dashboard "
                "all use that working connection.",
                409,
            )
        if not handle.lock.acquire(timeout=2):
            raise AppError(
                f"The working connection {handle.name} is already running a statement.",
                409,
            )
        try:
            handle.last_used = time.time()
            if action:
                try:
                    handle.conn.action = action[:32]
                except oracledb.Error:
                    pass
            yield handle
            handle.last_used = time.time()
        finally:
            handle.lock.release()

    def connect(
        self,
        sid: str,
        record: dict,
        secrets: dict,
        confirm: bool,
        max_open: int = 5,
        expire_time: int | None = None,
    ) -> tuple[DbHandle, bool]:
        lock = self._sid_lock(sid)
        with lock:
            existing = self._find(sid, record["id"])
            if existing is not None:
                if existing.open_transaction and confirm:
                    try:
                        existing.conn.rollback()
                    except oracledb.Error:
                        pass
                with self._lock:
                    self._active[sid] = record["id"]
                return existing, False
            with self._lock:
                open_count = sum(1 for key in self._handles if key[0] == sid)
            if open_count >= max_open:
                raise AppError(
                    f"This browser already has {open_count} database sessions open. "
                    f"The limit is {max_open}. Disconnect one before opening another. "
                    "Change the limit in Settings.",
                    409,
                )
            connection = open_connection(record, secrets, self.settings, expire_time=expire_time)
            if expire_time:
                try:
                    connection.action = "Connected"
                except oracledb.Error:
                    pass
            try:
                info = describe_session(connection)
            except Exception:
                info = {"session_user": record["username"], "db_name": "", "service_name": ""}
            handle = DbHandle(
                sid=sid,
                connection_id=record["id"],
                conn=connection,
                lock=threading.Lock(),
                opened_at=time.time(),
                last_used=time.time(),
                read_only=bool(record.get("read_only")),
                name=record["name"],
                db_user=str(info.get("session_user") or record["username"]),
                db_name=str(info.get("db_name") or ""),
                service_name=str(info.get("service_name") or ""),
            )
            with self._lock:
                self._handles[(sid, record["id"])] = handle
                self._active[sid] = record["id"]
            return handle, False

    def activate(self, sid: str, connection_id: str) -> DbHandle:
        handle = self._find(sid, connection_id)
        if handle is None:
            raise AppError(
                "That saved connection is not open. Click Connect before using it as the working connection.",
                409,
            )
        with self._lock:
            self._active[sid] = connection_id
        return handle

    def disconnect(self, sid: str, confirm: bool, connection_id: str | None = None) -> bool:
        if connection_id is None:
            current = self.get(sid)
            connection_id = current.connection_id if current else None
        if not connection_id:
            return False
        handle = self._find(sid, connection_id)
        if handle and handle.open_transaction and not confirm:
            raise AppError(
                f"The connection {handle.name} has an open transaction.",
                409,
                {"code": "open_transaction", "connection": handle.name},
            )
        with self._lock:
            handle = self._handles.pop((sid, connection_id), None)
            if handle is not None and self._active.get(sid) == connection_id:
                remaining = [key[1] for key in self._handles if key[0] == sid]
                if remaining:
                    self._active[sid] = remaining[0]
                else:
                    self._active.pop(sid, None)
        if handle is None:
            return False
        rolled = handle.open_transaction
        self._close(handle, rollback=True)
        return rolled

    def drop_connection(self, connection_id: str, confirm: bool) -> bool:
        with self._lock:
            targets = [handle for handle in self._handles.values() if handle.connection_id == connection_id]
        if any(handle.open_transaction for handle in targets) and not confirm:
            raise AppError(
                "A browser session has an open transaction on this connection.",
                409,
                {"code": "open_transaction"},
            )
        rolled = False
        for handle in targets:
            rolled = rolled or handle.open_transaction
            with self._lock:
                self._handles.pop((handle.sid, handle.connection_id), None)
                if self._active.get(handle.sid) == handle.connection_id:
                    remaining = [key[1] for key in self._handles if key[0] == handle.sid]
                    if remaining:
                        self._active[handle.sid] = remaining[0]
                    else:
                        self._active.pop(handle.sid, None)
            self._close(handle, rollback=True)
        return rolled

    def update_labels(self, connection_id: str, name: str, read_only: bool) -> None:
        with self._lock:
            for handle in self._handles.values():
                if handle.connection_id == connection_id:
                    handle.name = name
                    handle.read_only = read_only

    def discard(self, sid: str) -> None:
        with self._lock:
            connection_id = self._active.get(sid)
            handle = self._handles.pop((sid, connection_id), None) if connection_id else None
            if handle is not None:
                remaining = [key[1] for key in self._handles if key[0] == sid]
                if remaining:
                    self._active[sid] = remaining[0]
                else:
                    self._active.pop(sid, None)
        if handle is not None:
            self._close(handle, rollback=False)

    def close_browser(self, sid: str) -> None:
        with self._lock:
            keys = [key for key in self._handles if key[0] == sid]
            handles = [self._handles.pop(key) for key in keys]
            self._active.pop(sid, None)
        for handle in handles:
            self._close(handle, rollback=True)

    def _find(self, sid: str, connection_id: str) -> DbHandle | None:
        with self._lock:
            return self._handles.get((sid, connection_id))

    def close_all(self) -> None:
        with self._lock:
            handles = list(self._handles.values())
            self._handles.clear()
        for handle in handles:
            self._close(handle, rollback=True)

    def _sid_lock(self, sid: str) -> threading.Lock:
        with self._lock:
            lock = self._sid_locks.get(sid)
            if lock is None:
                lock = threading.Lock()
                self._sid_locks[sid] = lock
            return lock

    def _close(self, handle: DbHandle, rollback: bool) -> None:
        for cursor in list(handle.page_cursors.values()):
            try:
                cursor.close()
            except Exception:
                pass
        handle.page_cursors.clear()
        try:
            if rollback:
                try:
                    handle.conn.rollback()
                except oracledb.Error:
                    pass
            handle.conn.close()
        except oracledb.Error:
            log.warning("closing %s failed", handle.name)
        log.info("oracle disconnect name=%s user=%s", handle.name, handle.db_user)
