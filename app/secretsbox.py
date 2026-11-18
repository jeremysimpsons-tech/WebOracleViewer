"""Encrypt database passwords stored on disk."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.errors import AppError


class SecretBox:
    def __init__(self, key_bytes: bytes) -> None:
        self._fernet = Fernet(key_bytes)

    @classmethod
    def from_file(cls, path) -> "SecretBox":
        raw = path.read_text(encoding="utf-8").strip().encode("ascii")
        try:
            Fernet(raw)
        except (ValueError, TypeError) as exc:
            raise AppError(
                "The master key file is not a valid encryption key. "
                "Restore data/master.key from backup.",
                500,
            ) from exc
        return cls(raw)

    def encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise AppError(
                "A saved database password cannot be decrypted with the current master key. "
                "Edit the connection and enter the password again.",
                409,
            ) from exc
