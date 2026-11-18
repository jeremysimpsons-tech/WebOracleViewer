"""FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api import router as api_router
from app.config import get_settings, load_env
from app.db import SessionRegistry
from app.errors import AppError
from app.secretsbox import SecretBox
from app.library import QueryLibrary
from app.prefs import ClientPrefs
from app.store import ConnectionStore
from app.web import router as web_router

PACKAGE_DIR = __import__("pathlib").Path(__file__).resolve().parent
CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "style-src-attr 'unsafe-inline'; "
    "script-src 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


class SecurityHeaders:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Content-Security-Policy"] = CSP
                headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
                path = scope.get("path") or ""
                if not path.startswith("/static/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app() -> FastAPI:
    load_env()
    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_storage()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not settings.wallet_root:
        logging.getLogger("e1p").warning(
            "WALLET_ROOT is unset. Wallet directories are not confined to one folder."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.registry.start()
        try:
            yield
        finally:
            app.state.registry.stop()

    app = FastAPI(
        title="E1P DB Viewer",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.registry = SessionRegistry(settings)
    app.state.store = ConnectionStore(settings, SecretBox.from_file(settings.master_key_file))
    app.state.library = QueryLibrary(settings)
    app.state.prefs = ClientPrefs(settings)
    from app.security import LoginLimiter

    app.state.limiter = LoginLimiter(settings.login_max_failures, settings.login_window_seconds)

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> JSONResponse:
        body = {"detail": exc.message, **exc.extra}
        return JSONResponse(status_code=exc.status, content=body)

    @app.get("/healthz")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(web_router)
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")
    app.add_middleware(SecurityHeaders)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="e1p_session",
        max_age=settings.session_max_age_minutes * 60,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    return app
