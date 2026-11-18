"""Saved Oracle connection profiles. Passwords are encrypted before they touch disk."""

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
from app.jdbc import parse_connect_string
from app.secretsbox import SecretBox

_NAME = re.compile(r"[A-Za-z0-9 ._:#@()\-]{1,80}")
_USER = re.compile(r"[A-Za-z][A-Za-z0-9_$#]{0,127}")
_HOST = re.compile(r"(?:[A-Za-z0-9._-]+|\[[0-9A-Fa-f:]+\])")
_IDENT = re.compile(r"[A-Za-z0-9_.$#\-]+")
_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_.$#\-]*")
_MAX_CONNECTIONS = 200
_FINGERPRINT = (
    "username",
    "password_enc",
    "mode",
    "host",
    "port",
    "service_name",
    "sid",
    "protocol",
    "ssl_server_dn_match",
    "wallet_dir",
    "wallet_password_enc",
    "tns_alias",
    "connect_string",
    "role",
)


def fingerprint(record: dict) -> tuple:
    return tuple(record.get(key) for key in _FINGERPRINT)


class ConnectionStore:
    def __init__(self, settings: Settings, box: SecretBox) -> None:
        self.path: Path = settings.data_dir / "connections.json"
        self._box = box
        self._settings = settings
        self._lock = threading.Lock()

    def list_public(self) -> list[dict]:
        with self._lock:
            records = self._read()["connections"]
        return sorted((self._public(item) for item in records), key=lambda item: item["name"].lower())

    def get(self, connection_id: str) -> dict:
        with self._lock:
            return self._require(self._read(), connection_id)

    def create(self, payload: dict) -> dict:
        with self._lock:
            data = self._read()
            if len(data["connections"]) >= _MAX_CONNECTIONS:
                raise AppError(f"The connection list is limited to {_MAX_CONNECTIONS} entries.")
            record = self._build(payload, existing=None)
            self._ensure_unique_name(data["connections"], record["name"], None)
            data["connections"].append(record)
            self._write(data)
        return self._public(record)

    def update(self, connection_id: str, payload: dict) -> tuple[dict, bool]:
        """Return the public record and whether the Oracle target itself changed."""
        with self._lock:
            data = self._read()
            current = self._require(data, connection_id)
            record = self._build(payload, existing=current)
            record["id"] = current["id"]
            record["created_at"] = current["created_at"]
            self._ensure_unique_name(data["connections"], record["name"], connection_id)
            changed = fingerprint(current) != fingerprint(record)
            data["connections"] = [
                record if item["id"] == connection_id else item for item in data["connections"]
            ]
            self._write(data)
        return self._public(record), changed

    def delete(self, connection_id: str) -> None:
        with self._lock:
            data = self._read()
            self._require(data, connection_id)
            data["connections"] = [item for item in data["connections"] if item["id"] != connection_id]
            self._write(data)

    def compile(self, payload: dict, connection_id: str | None = None) -> tuple[dict, dict]:
        """Validate a form and return a record plus decrypted secrets, without saving."""
        with self._lock:
            existing = None
            if connection_id:
                existing = self._require(self._read(), connection_id)
            record = self._build(payload, existing)
            if existing:
                record["id"] = existing["id"]
                record["created_at"] = existing["created_at"]
            return record, self.secrets_for(record)

    def secrets_for(self, record: dict) -> dict:
        password = self._box.decrypt(record["password_enc"]) if record.get("password_enc") else ""
        wallet_password = ""
        if record.get("wallet_password_enc"):
            wallet_password = self._box.decrypt(record["wallet_password_enc"])
        return {"password": password, "wallet_password": wallet_password}

    def _build(self, payload: dict, existing: dict | None) -> dict:
        name = _clean_name(str(payload.get("name", "")))
        username = str(payload.get("username", "")).strip()
        if not _USER.fullmatch(username):
            raise AppError("The database username must be a simple Oracle identifier, such as ADMIN or HR.")
        mode = str(payload.get("mode", "")).strip()
        if mode not in {"wallet", "classic", "connect_string"}:
            raise AppError("Choose a wallet, classic, or JDBC connect-string connection.")
        password = str(payload.get("password", ""))
        if "\x00" in password or len(password) > 1024:
            raise AppError("The database password is empty or too long.")
        if not password:
            if existing and existing.get("password_enc"):
                password_enc = existing["password_enc"]
            else:
                raise AppError("Enter the database password.")
        else:
            password_enc = self._reuse_secret(existing, "password_enc", password)
        wallet_password = str(payload.get("wallet_password", ""))
        if "\x00" in wallet_password or len(wallet_password) > 1024:
            raise AppError("The wallet password is too long.")
        if wallet_password:
            wallet_password_enc = self._reuse_secret(existing, "wallet_password_enc", wallet_password)
        elif existing and existing.get("wallet_password_enc"):
            wallet_password_enc = existing["wallet_password_enc"]
        else:
            wallet_password_enc = ""
        role = str(payload.get("role", "")).strip().upper()
        if role not in {"", "SYSDBA", "SYSOPER"}:
            raise AppError("Role must be blank, SYSDBA, or SYSOPER.")
        protocol = str(payload.get("protocol", "tcp")).strip().lower() or "tcp"
        if protocol not in {"tcp", "tcps"}:
            raise AppError("Protocol must be tcp or tcps.")
        ssl_match = bool(payload.get("ssl_server_dn_match", True))
        host = str(payload.get("host", "")).strip()
        service_name = str(payload.get("service_name", "")).strip()
        sid = str(payload.get("sid", "")).strip()
        tns_alias = str(payload.get("tns_alias", "")).strip()
        connect_string = " ".join(str(payload.get("connect_string", "")).split())
        wallet_dir = str(payload.get("wallet_dir", "")).strip()
        port = payload.get("port")
        if mode == "classic":
            host, port, service_name, sid, protocol = _classic_fields(
                host, port, service_name, sid, protocol
            )
            tns_alias = ""
            connect_string = ""
            wallet_dir = ""
            wallet_password_enc = ""
        elif mode == "wallet":
            if not wallet_dir:
                raise AppError("Enter the wallet directory on this server.")
            if not tns_alias or not _ALIAS.fullmatch(tns_alias):
                raise AppError("Enter the TNS alias from the wallet tnsnames.ora, such as mydb_high.")
            known_secret = wallet_password or wallet_password_enc or None
            wallet_dir = str(_wallet_dir(wallet_dir, self._settings.wallet_root, known_secret))
            host = ""
            port = None
            service_name = ""
            sid = ""
            protocol = "tcps"
            connect_string = ""
        else:
            if not connect_string:
                raise AppError("Paste a JDBC URL or Oracle connect string.")
            parsed = parse_connect_string(connect_string)
            if parsed.wallet_dir:
                known_secret = wallet_password or wallet_password_enc or None
                wallet_dir = str(_wallet_dir(parsed.wallet_dir, self._settings.wallet_root, known_secret))
            else:
                wallet_dir = ""
                wallet_password_enc = ""
            host = parsed.host or ""
            port = parsed.port
            service_name = parsed.service_name or ""
            sid = parsed.sid or ""
            protocol = parsed.protocol
            tns_alias = parsed.tns_alias or ""
        now = _now()
        return {
            "id": str(uuid.uuid4()),
            "name": name,
            "username": username,
            "password_enc": password_enc,
            "mode": mode,
            "host": host,
            "port": port,
            "service_name": service_name,
            "sid": sid,
            "protocol": protocol,
            "ssl_server_dn_match": ssl_match,
            "wallet_dir": wallet_dir,
            "wallet_password_enc": wallet_password_enc,
            "tns_alias": tns_alias,
            "connect_string": connect_string if mode == "connect_string" else "",
            "role": role,
            "read_only": bool(payload.get("read_only", False)),
            "created_at": now,
            "updated_at": now,
        }

    def _reuse_secret(self, existing: dict | None, field: str, plaintext: str) -> str:
        if existing and existing.get(field):
            try:
                if self._box.decrypt(existing[field]) == plaintext:
                    return existing[field]
            except AppError:
                pass
        return self._box.encrypt(plaintext)

    def _public(self, record: dict) -> dict:
        hidden = {"password_enc", "wallet_password_enc"}
        public = {key: value for key, value in record.items() if key not in hidden}
        public["password_set"] = bool(record.get("password_enc"))
        public["wallet_password_set"] = bool(record.get("wallet_password_enc"))
        return public

    def _require(self, data: dict, connection_id: str) -> dict:
        for item in data["connections"]:
            if item["id"] == connection_id:
                return item
        raise AppError("That saved connection no longer exists.", 404)

    def _ensure_unique_name(self, records: list[dict], name: str, ignore_id: str | None) -> None:
        folded = name.casefold()
        for item in records:
            if item["id"] != ignore_id and item["name"].casefold() == folded:
                raise AppError(f"A connection named {item['name']} already exists.", 409)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "connections": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(
                "The connection store is unreadable. Restore data/connections.json from backup.",
                500,
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("connections"), list):
            raise AppError("The connection store has an unexpected shape.", 500)
        return data

    def _write(self, data: dict) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        temporary = self.path.with_suffix(".json.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def resolve_wallet_dir(path: str, root: Path | None, wallet_password: str | None = None) -> Path:
    return _wallet_dir(path, root, wallet_password)


def _wallet_dir(path: str, root: Path | None, wallet_password: str | None) -> Path:
    if not path or "\x00" in path:
        raise AppError("Enter the wallet directory on this server.")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise AppError("The wallet directory must be an absolute path on this server.")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise AppError("The wallet directory does not exist on this server.") from exc
    if not resolved.is_dir():
        raise AppError("The wallet path is not a directory.")
    if root is not None:
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise AppError(f"The wallet directory must be inside {root.resolve()}.") from exc
    try:
        names = {item.name.lower() for item in resolved.iterdir()}
    except OSError as exc:
        raise AppError("The service account cannot read the wallet directory.") from exc
    if "tnsnames.ora" not in names:
        raise AppError("The wallet directory has no tnsnames.ora.")
    if "cwallet.sso" not in names and "ewallet.p12" not in names:
        raise AppError("The wallet directory has no cwallet.sso or ewallet.p12.")
    if _needs_wallet_password(resolved) and not wallet_password:
        raise AppError("This wallet has ewallet.p12 and no cwallet.sso. Enter the wallet password.")
    return resolved


def _needs_wallet_password(path: str | Path) -> bool:
    directory = Path(path)
    names = {item.name.lower() for item in directory.iterdir()}
    return "ewallet.p12" in names and "cwallet.sso" not in names


def _classic_fields(host: str, port, service_name: str, sid: str, protocol: str) -> tuple:
    if not _HOST.fullmatch(host):
        raise AppError("Enter a hostname or IP address without a port.")
    if port in (None, ""):
        port = 1522 if protocol == "tcps" else 1521
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise AppError("Enter a numeric listener port.") from exc
    if port < 1 or port > 65535:
        raise AppError("The listener port must be between 1 and 65535.")
    if service_name and not _IDENT.fullmatch(service_name):
        raise AppError("The service name contains unsupported characters.")
    if sid and not re.fullmatch(r"[A-Za-z0-9_$#]+", sid):
        raise AppError("The SID contains unsupported characters.")
    if service_name and sid:
        raise AppError("Enter a service name or a SID, not both.")
    if not service_name and not sid:
        raise AppError("Enter a service name or a SID.")
    return host, port, service_name, sid, protocol


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned or not _NAME.fullmatch(cleaned):
        raise AppError(
            "The connection name can use letters, numbers, spaces, and . _ : # @ ( ) - and must be 1–80 characters."
        )
    return cleaned


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
