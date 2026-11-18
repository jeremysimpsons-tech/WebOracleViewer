"""Application errors that are safe to show to the signed-in operator."""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, status: int = 400, extra: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra or {}


class ConfigError(RuntimeError):
    """The process cannot start because configuration is missing or invalid."""


class QueryFailed(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_PRIVILEGE_CODES = (
    "ORA-00942",  # table or view does not exist
    "ORA-01031",  # insufficient privileges
    "ORA-00904",  # invalid identifier (often a column the account cannot see)
    "ORA-01039",  # insufficient privileges on underlying objects
)


def is_missing_privilege(message: str) -> bool:
    return any(code in message for code in _PRIVILEGE_CODES)


def first_line(message: str, limit: int = 500) -> str:
    line = message.strip().splitlines()[0] if message.strip() else "Unknown database error"
    return line[:limit]


def redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            redacted = redacted.replace(secret, "********")
    return redacted
