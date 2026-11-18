"""Process configuration.

The .env parser does not expand ``$``. Argon2 password hashes contain dollar
signs, and a shell-style parser would destroy them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load_env() -> None:
    explicit = os.environ.get("ENV_FILE")
    if explicit is not None:
        path = Path(explicit)
        if path.is_file():
            _apply_env_file(path)
        return
    for candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        if candidate.is_file():
            _apply_env_file(candidate)
            return


def _apply_env_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _KEY.fullmatch(key) or key in os.environ:
            continue
        os.environ[key] = _unquote(value.strip())


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def upsert_env_value(path: Path, key: str, value: str) -> None:
    if not _KEY.fullmatch(key):
        raise ConfigError(f"Invalid environment name {key}")
    if "'" in value or "\n" in value or "\r" in value:
        raise ConfigError(f"Cannot store {key} because the value contains a quote or newline")
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    rendered = f"{key}='{value}'"
    prefix = f"{key}="
    export_prefix = f"export {key}="
    found = False
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) or stripped.startswith(export_prefix):
            updated.append(rendered)
            found = True
        else:
            updated.append(line)
    if not found:
        if updated and updated[-1] != "":
            updated.append("")
        updated.append(rendered)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < low or value > high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def _path(name: str, default: str) -> Path:
    raw = os.environ.get(name, default).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


@dataclass(frozen=True)
class Settings:
    bind_host: str
    bind_port: int
    session_secret: str
    admin_password_hash: str
    data_dir: Path
    master_key_file: Path
    cookie_secure: bool
    session_max_age_minutes: int
    db_call_timeout_ms: int
    db_session_idle_seconds: int
    max_fetch_rows: int
    wallet_root: Path | None
    audit_sql_text: bool
    login_max_failures: int
    login_window_seconds: int
    forwarded_allow_ips: str
    ssl_certfile: str
    ssl_keyfile: str
    log_level: str

    def ensure_storage(self) -> None:
        from cryptography.fernet import Fernet

        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        if not self.master_key_file.exists():
            self.master_key_file.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            fd = os.open(self.master_key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key + b"\n")
        else:
            try:
                os.chmod(self.master_key_file, 0o600)
            except OSError:
                pass
        if self.wallet_root is not None:
            self.wallet_root.mkdir(parents=True, exist_ok=True)


def _validate_hash(stored: str) -> None:
    if not stored.startswith("$argon2"):
        raise ConfigError(
            "ADMIN_PASSWORD_HASH must be an argon2 hash. Run: python -m app.admin_password"
        )
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerifyMismatchError

    try:
        PasswordHasher().verify(stored, "startup-format-check")
    except VerifyMismatchError:
        return
    except InvalidHashError as exc:
        raise ConfigError("ADMIN_PASSWORD_HASH is not a valid argon2 hash") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if len(secret) < 32:
        raise ConfigError(
            "SESSION_SECRET must be at least 32 characters. Run scripts/install.sh to create .env."
        )
    admin_hash = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
    if not admin_hash:
        raise ConfigError(
            "ADMIN_PASSWORD_HASH is not set. Run scripts/install.sh or python -m app.admin_password."
        )
    _validate_hash(admin_hash)
    host = os.environ.get("BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if not re.fullmatch(r"[A-Za-z0-9.:_-]+", host):
        raise ConfigError("BIND_HOST contains unsupported characters")
    cert = os.environ.get("SSL_CERTFILE", "").strip()
    key = os.environ.get("SSL_KEYFILE", "").strip()
    if bool(cert) != bool(key):
        raise ConfigError("Set both SSL_CERTFILE and SSL_KEYFILE, or leave both empty")
    for label, file_name in (("SSL_CERTFILE", cert), ("SSL_KEYFILE", key)):
        if file_name and not Path(file_name).is_file():
            raise ConfigError(f"{label} does not exist: {file_name}")
    wallet_raw = os.environ.get("WALLET_ROOT", "").strip()
    wallet_root = _path("WALLET_ROOT", wallet_raw) if wallet_raw else None
    settings = Settings(
        bind_host=host,
        bind_port=_int("BIND_PORT", 8080, 1, 65535),
        session_secret=secret,
        admin_password_hash=admin_hash,
        data_dir=_path("DATA_DIR", "data"),
        master_key_file=_path("MASTER_KEY_FILE", "data/master.key"),
        cookie_secure=_bool("COOKIE_SECURE", False),
        session_max_age_minutes=_int("SESSION_MAX_AGE_MINUTES", 480, 5, 10080),
        db_call_timeout_ms=_int("DB_CALL_TIMEOUT_MS", 120_000, 1_000, 3_600_000),
        db_session_idle_seconds=_int("DB_SESSION_IDLE_SECONDS", 1800, 60, 86_400),
        max_fetch_rows=_int("MAX_FETCH_ROWS", 2000, 1, 10_000),
        wallet_root=wallet_root,
        audit_sql_text=_bool("AUDIT_SQL_TEXT", False),
        login_max_failures=_int("LOGIN_MAX_FAILURES", 8, 3, 50),
        login_window_seconds=_int("LOGIN_WINDOW_SECONDS", 600, 60, 86_400),
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1").strip()
        or "127.0.0.1",
        ssl_certfile=cert,
        ssl_keyfile=key,
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
    return settings
