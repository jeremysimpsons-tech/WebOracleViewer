"""Sign-in throttling and the admin password check."""

from __future__ import annotations

import logging
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from starlette.requests import Request

from app.errors import AppError

log = logging.getLogger("e1p.auth")
_hasher = PasswordHasher()


def verify_admin_password(password: str, stored_hash: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        log.exception("admin password hash could not be checked")
        return False


def check_csrf(request: Request) -> None:
    expected = str(request.session.get("csrf") or "")
    sent = request.headers.get("x-csrf-token") or ""
    if (
        not expected
        or not sent
        or len(sent) != len(expected)
        or not secrets.compare_digest(sent, expected)
    ):
        raise AppError("The page token expired. Reload the page and try again.", 403)


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return str(token)


def rotate_session(request: Request) -> str:
    for key in list(request.session.keys()):
        request.session.pop(key, None)
    request.session["auth"] = True
    request.session["sid"] = secrets.token_urlsafe(32)
    request.session["csrf"] = secrets.token_urlsafe(32)
    return str(request.session["csrf"])


class LoginLimiter:
    def __init__(self, max_failures: int, window_seconds: int) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._failures.get(ip, []) if now - stamp < self.window_seconds]
            self._failures[ip] = recent
            if len(recent) >= self.max_failures:
                raise AppError(
                    "Too many sign-in attempts. Wait a few minutes and try again.",
                    429,
                )

    def fail(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._failures.get(ip, []) if now - stamp < self.window_seconds]
            recent.append(now)
            self._failures[ip] = recent

    def clear(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


def client_ip(request: Request) -> str:
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host
