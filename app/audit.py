"""Append-only operator audit file. SQL text is omitted unless explicitly enabled."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from app.config import Settings

log = logging.getLogger("e1p.audit")
_lock = threading.Lock()


def audit(settings: Settings, event: str, **fields: object) -> None:
    if not settings.audit_sql_text:
        fields.pop("sql", None)
    record = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "event": event,
        **fields,
    }
    line = json.dumps(record, default=str, ensure_ascii=True)
    path = settings.data_dir / "audit.log"
    try:
        with _lock:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError:
        log.warning("audit log could not be written")
