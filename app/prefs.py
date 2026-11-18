"""Operator choices for how the viewer behaves as a database client."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from app.config import Settings
from app.errors import AppError

DEFAULTS = {
    "block_locking_sql": True,
    "rollback_script_on_error": True,
    "max_open_connections": 5,
    "tag_sessions": True,
}


class ClientPrefs:
    def __init__(self, settings: Settings) -> None:
        self.path: Path = settings.data_dir / "client-prefs.json"
        self._lock = threading.Lock()
        self._values = dict(DEFAULTS)
        self._load()

    def get(self) -> dict:
        with self._lock:
            return dict(self._values)

    def update(self, payload: dict) -> dict:
        clean = {
            "block_locking_sql": bool(payload.get("block_locking_sql")),
            "rollback_script_on_error": bool(payload.get("rollback_script_on_error")),
            "max_open_connections": _max_open(payload.get("max_open_connections")),
            "tag_sessions": bool(payload.get("tag_sessions")),
        }
        with self._lock:
            self._values = clean
            self._write(clean)
            return dict(clean)

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        merged = dict(DEFAULTS)
        if "block_locking_sql" in raw:
            merged["block_locking_sql"] = bool(raw["block_locking_sql"])
        if "rollback_script_on_error" in raw:
            merged["rollback_script_on_error"] = bool(raw["rollback_script_on_error"])
        if "tag_sessions" in raw:
            merged["tag_sessions"] = bool(raw["tag_sessions"])
        if "max_open_connections" in raw:
            try:
                merged["max_open_connections"] = _max_open(raw["max_open_connections"])
            except AppError:
                pass
        self._values = merged

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


def _max_open(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise AppError("The open-connection limit must be a whole number from 1 to 30.") from exc
    if number < 1 or number > 30:
        raise AppError("The open-connection limit must be a whole number from 1 to 30.")
    return number
