"""Auth router: login, register, lightweight trial, and session-revoking logout."""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import db
from webapp.csrf import verify_csrf
from webapp.routers.portal import build_base_context, get_public_templates
from webapp.sandbox.base import SlidingWindowRateLimiter
from webapp.sessions import (
    clear_all_sessions,
    create_trial_session,
    create_user_session,
    get_client_ip,
    get_current_user,
)
from webapp.subjects import load_site_meta

router = APIRouter(dependencies=[Depends(verify_csrf)])

_SOURCE_RE = re.compile(r"^[a-z0-9_-]{1,24}$")

login_rate_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=300.0)
register_rate_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=600.0)
trial_rate_limiter = SlidingWindowRateLimiter(max_requests=15, window_seconds=300.0)


def validate_next_url(raw: str | None) -> str:
    """Validate redirect target strictly as a safe local relative path."""
    if not raw or not isinstance(raw, str):
        return "/"
    candidate = raw.strip()
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    if "\\" in candidate:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        return "/"
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return "/"
    return candidate


def _sanitize_from_source(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip().lower()
    return cleaned if _SOURCE_RE.fullmatch(cleaned) else None


def _render_auth(
    request: Request,
    *,
    mode: str,
    error: str | None = None,
    next_url: str = "/",
    from_source: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    user = get_current_user(request)
    site = load_site_meta()
    title_prefix = "Přihlášení" if mode == "login" else "Registrace"
    ctx = build_base_context(
        request,
        user=user,
        page_title=f"{title_prefix} | {site['title']}",
        active_nav="auth",
    )
    ctx.update(
        {
            "mode": mode,
            "error": error,
            "next_url": next_url,
            "from_source": from_source,
        }
    )
    return get_public_templates().TemplateResponse(
        request,
        "auth.html",
        ctx,
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str | None = Query(default=None),  # noqa: A002
    from_source: str | None = Query(default=None, alias="from"),
) -> HTMLResponse:
    return _render_auth(
        request,
        mode="login",
        next_url=validate_next_url(next),
        from_source=_sanitize_from_source(from_source),
    )


@router.post("/login", response_model=None)
def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next_param: str = Form(default="/", alias="next"),
    from_source: str | None = Form(default=None, alias="from"),
) -> Any:
    safe_next = validate_next_url(next_param)
    safe_from = _sanitize_from_source(from_source)

    allowed, retry_after = login_rate_limiter.check(
        f"auth:login:{get_client_ip(request)}"
    )
    if not allowed:
        retry_sec = max(1, math.ceil(retry_after))
        resp = _render_auth(
            request,
            mode="login",
            error=f"Příliš mnoho pokusů o přihlášení. Zkus to za {retry_sec} s.",
            next_url=safe_next,
            from_source=safe_from,
            status_code=429,
        )
        resp.headers["Retry-After"] = str(retry_sec)
        return resp

    user = db.authenticate_user(username, password)
    if user is None:
        return _render_auth(
            request,
            mode="login",
            error="Neplatné uživatelské jméno nebo heslo.",
            next_url=safe_next,
            from_source=safe_from,
            status_code=400,
        )

    response = RedirectResponse(url=safe_next, status_code=303)
    create_user_session(response, request, user)
    return response


@router.get("/register", response_class=HTMLResponse)
def register_page(
    request: Request,
    next: str | None = Query(default=None),  # noqa: A002
    from_source: str | None = Query(default=None, alias="from"),
) -> HTMLResponse:
    return _render_auth(
        request,
        mode="register",
        next_url=validate_next_url(next),
        from_source=_sanitize_from_source(from_source),
    )


@router.post("/register", response_model=None)
def register_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    password_confirm: str | None = Form(default=None),
    next_param: str = Form(default="/", alias="next"),
    from_source: str | None = Form(default=None, alias="from"),
) -> Any:
    safe_next = validate_next_url(next_param)
    safe_from = _sanitize_from_source(from_source)

    allowed, retry_after = register_rate_limiter.check(
        f"auth:register:{get_client_ip(request)}"
    )
    if not allowed:
        retry_sec = max(1, math.ceil(retry_after))
        resp = _render_auth(
            request,
            mode="register",
            error=f"Příliš mnoho pokusů o registraci. Zkus to za {retry_sec} s.",
            next_url=safe_next,
            from_source=safe_from,
            status_code=429,
        )
        resp.headers["Retry-After"] = str(retry_sec)
        return resp

    if password_confirm is not None and password != password_confirm:
        return _render_auth(
            request,
            mode="register",
            error="Hesla se neshodují.",
            next_url=safe_next,
            from_source=safe_from,
            status_code=400,
        )

    try:
        user = db.create_user(username, password, role="user")
    except ValueError as exc:
        return _render_auth(
            request,
            mode="register",
            error=str(exc),
            next_url=safe_next,
            from_source=safe_from,
            status_code=400,
        )

    response = RedirectResponse(url=safe_next, status_code=303)
    create_user_session(response, request, user)
    return response


@router.post("/trial", response_model=None)
def trial_start(
    request: Request,
    next_param: str = Form(default="/", alias="next"),
) -> Any:
    safe_next = validate_next_url(next_param)
    accept = request.headers.get("accept", "")
    wants_json = "application/json" in accept and "text/html" not in accept

    allowed, retry_after = trial_rate_limiter.check(
        f"auth:trial:{get_client_ip(request)}"
    )
    if not allowed:
        retry_sec = max(1, math.ceil(retry_after))
        if wants_json:
            return JSONResponse(
                {"detail": f"Příliš mnoho požadavků. Zkus to za {retry_sec} s."},
                status_code=429,
                headers={"Retry-After": str(retry_sec)},
            )
        resp = _render_auth(
            request,
            mode="login",
            error=f"Příliš mnoho požadavků. Zkus to za {retry_sec} s.",
            next_url=safe_next,
            status_code=429,
        )
        resp.headers["Retry-After"] = str(retry_sec)
        return resp

    if wants_json:
        response: Any = JSONResponse({"ok": True, "trial": True, "next": safe_next})
    else:
        response = RedirectResponse(url=safe_next, status_code=303)
    create_trial_session(response, request)
    return response


@router.post("/logout", response_model=None)
def logout_submit(request: Request) -> Any:
    user = get_current_user(request)
    if user and user.get("is_authenticated") and user.get("id") is not None:
        db.increment_session_version(int(user["id"]))

    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        response: Any = JSONResponse({"ok": True})
    else:
        response = RedirectResponse(url="/", status_code=303)
    clear_all_sessions(response, request)
    return response
