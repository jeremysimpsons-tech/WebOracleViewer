"""HTML pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from app import __version__
from app.security import ensure_csrf

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@router.get("/login")
def login_page(request: Request):
    if request.session.get("auth"):
        return RedirectResponse("/", status_code=303)
    token = ensure_csrf(request)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"csrf_token": token, "version": __version__},
    )


@router.get("/")
def app_page(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    token = ensure_csrf(request)
    return templates.TemplateResponse(
        request,
        "app.html",
        {"csrf_token": token, "version": __version__},
    )
