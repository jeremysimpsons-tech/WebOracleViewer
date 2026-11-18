"""Translate JDBC and Oracle Net connect strings into driver fields.

Accepted forms include ``jdbc:oracle:thin:@host:port/service``, SID syntax,
``tcps://`` easy connect, a full ``(DESCRIPTION=...)`` string, and
``alias?TNS_ADMIN=/path/to/wallet``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote

from app.errors import AppError

_PREFIX = re.compile(r"^jdbc:oracle:thin:@", re.IGNORECASE)
_SID = re.compile(
    r"^(?P<host>\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._-]+):(?P<port>\d{1,5}):(?P<sid>[A-Za-z0-9_$#]+)$"
)
_EASY = re.compile(
    r"^(?:(?P<proto>tcp|tcps)://)?(?://)?"
    r"(?P<host>\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._-]+)"
    r"(?::(?P<port>\d{1,5}))?"
    r"(?:/(?P<service>[A-Za-z0-9_.$#-]+))?$",
    re.IGNORECASE,
)
_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_.$#-]*$")
_PROTOCOL = re.compile(r"\(PROTOCOL\s*=\s*(TCP|TCPS)\)", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedConnect:
    dsn: str
    protocol: str
    host: str | None = None
    port: int | None = None
    service_name: str | None = None
    sid: str | None = None
    wallet_dir: str | None = None
    tns_alias: str | None = None


def parse_connect_string(value: str) -> ParsedConnect:
    text = " ".join(value.replace("\x00", " ").split())
    if not text:
        raise AppError("Enter a JDBC URL or Oracle connect string.")
    if len(text) > 4000:
        raise AppError("The connect string is longer than 4000 characters.")
    if text.lower().startswith("jdbc:"):
        if not _PREFIX.match(text):
            raise AppError(
                "Only jdbc:oracle:thin:@ URLs are supported. "
                "Other JDBC drivers cannot be used by this service."
            )
        text = _PREFIX.sub("", text, count=1)
    wallet_dir = None
    if text.startswith("("):
        body = text
        query = ""
    else:
        body, separator, query = text.partition("?")
        if not separator:
            query = ""
    if query:
        params = {key.lower(): unquote(values[-1]) for key, values in parse_qs(query, keep_blank_values=True).items()}
        wallet_dir = (params.get("tns_admin") or params.get("wallet_location") or "").strip() or None
        if wallet_dir and len(wallet_dir) >= 2 and wallet_dir[0] == wallet_dir[-1] and wallet_dir[0] in {"'", '"'}:
            wallet_dir = wallet_dir[1:-1]
    body = body.strip()
    if body.startswith("("):
        protocol = "tcp"
        found = _PROTOCOL.search(body)
        if found:
            protocol = found.group(1).lower()
        return ParsedConnect(dsn=body, protocol=protocol, wallet_dir=wallet_dir)
    sid_match = _SID.fullmatch(body)
    if sid_match:
        port = _port(sid_match.group("port"))
        host = sid_match.group("host")
        sid = sid_match.group("sid")
        return ParsedConnect(
            dsn=f"{host}:{port}:{sid}",
            protocol="tcp",
            host=host,
            port=port,
            sid=sid,
            wallet_dir=wallet_dir,
        )
    easy = _EASY.fullmatch(body)
    if easy and (easy.group("proto") or easy.group("port") or easy.group("service")):
        protocol = (easy.group("proto") or "tcp").lower()
        host = easy.group("host")
        port = _port(easy.group("port")) if easy.group("port") else (1522 if protocol == "tcps" else 1521)
        service = easy.group("service")
        dsn = f"{protocol}://{host}:{port}"
        if service:
            dsn += f"/{service}"
        return ParsedConnect(
            dsn=dsn,
            protocol=protocol,
            host=host,
            port=port,
            service_name=service,
            wallet_dir=wallet_dir,
        )
    if _ALIAS.fullmatch(body):
        return ParsedConnect(dsn=body, protocol="tcps" if wallet_dir else "tcp", tns_alias=body, wallet_dir=wallet_dir)
    raise AppError(
        "Could not parse that connect string. Use host:port/service, "
        "host:port:sid, tcps://host:port/service, a (DESCRIPTION=...) block, "
        "or a TNS alias with TNS_ADMIN."
    )


def _port(raw: str) -> int:
    value = int(raw)
    if value < 1 or value > 65535:
        raise AppError("The port in the connect string is not valid.")
    return value
