"""FastAPI app factory with strict CSP/Trusted Types, HSTS, and isolated /static."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

import db
from webapp.config import PUBLIC_DIR, load_settings
from webapp.cookie_utils import is_https_request
from webapp.csrf import attach_csrf_cookie_if_needed, get_or_create_csrf_token
from webapp.routers import (
    admin,
    auth,
    feedback,
    internal,
    portal,
    preferences,
    sandbox,
    study,
)

_CODE_ROUTE_RE = re.compile(r"^/predmet/[a-z0-9_-]{1,32}/code$")

_BASE_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "require-trusted-types-for 'script'; "
    "trusted-types 'none';"
)

_CODE_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "worker-src 'self' blob:; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "require-trusted-types-for 'script'; "
    "trusted-types 'none';"
)

_NO_STORE_PREFIXES = (
    "/admin",
    "/api/admin",
    "/api/feedback",
    "/api/preferences",
    "/internal",
    "/login",
    "/register",
)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    load_settings()
    db.init_db()
    yield


def _build_csp_for_path(path: str) -> str:
    if _CODE_ROUTE_RE.fullmatch(path):
        return _CODE_CSP
    return _BASE_CSP


def create_app() -> FastAPI:
    """Create and configure the UNI Suite FastAPI application."""
    load_settings()
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    application = FastAPI(
        title="UNI Suite",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    @application.middleware("http")
    async def security_and_csrf_cookie_middleware(
        request: Request, call_next: Any
    ) -> Response:
        get_or_create_csrf_token(request)
        response: Response = await call_next(request)
        attach_csrf_cookie_if_needed(request, response)

        response.headers["Content-Security-Policy"] = _build_csp_for_path(
            request.url.path
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        if is_https_request(request):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        if any(request.url.path.startswith(prefix) for prefix in _NO_STORE_PREFIXES):
            response.headers.setdefault("Cache-Control", "no-store")

        return response

    application.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")

    application.include_router(portal.router)
    application.include_router(auth.router)
    application.include_router(preferences.router)
    application.include_router(study.router)
    application.include_router(sandbox.router)
    application.include_router(feedback.router)
    application.include_router(admin.router)
    application.include_router(internal.router)

    return application


app = create_app()
