"""Start the service in the foreground. scripts/start.sh invokes this module."""

from __future__ import annotations

import inspect
import sys

import uvicorn

from app.errors import ConfigError
from app.main import create_app


def main() -> None:
    try:
        app = create_app()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None
    settings = app.state.settings
    kwargs = {
        "host": settings.bind_host,
        "port": settings.bind_port,
        "workers": 1,
        "proxy_headers": True,
        "forwarded_allow_ips": settings.forwarded_allow_ips,
        "log_level": settings.log_level.lower(),
    }
    if settings.ssl_certfile:
        kwargs["ssl_certfile"] = settings.ssl_certfile
        kwargs["ssl_keyfile"] = settings.ssl_keyfile
    signature = inspect.signature(uvicorn.run)
    if "server_header" in signature.parameters:
        kwargs["server_header"] = False
    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    main()
