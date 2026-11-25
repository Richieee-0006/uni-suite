"""Double-submit CSRF protection (TESTING=1 bypass ONLY, never User-Agent)."""

from __future__ import annotations

import hmac
import re
import secrets

from fastapi import HTTPException, Request, Response

from webapp.config import CSRF_MAX_AGE, is_testing
from webapp.cookie_utils import set_raw_cookie

CSRF_COOKIE_NAME = "uni_csrf"
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _is_valid_token_format(token: str | None) -> bool:
    return bool(token and _TOKEN_RE.fullmatch(token))


def get_or_create_csrf_token(request: Request) -> str:
    """Return existing CSRF token for this request or generate a fresh one."""
    existing = getattr(request.state, "csrf_token", None)
    if isinstance(existing, str) and _is_valid_token_format(existing):
        return existing

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if cookie_token is not None and _is_valid_token_format(cookie_token):
        request.state.csrf_token = cookie_token
        request.state.csrf_cookie_needs_set = False
        return cookie_token

    fresh = secrets.token_urlsafe(32)
    request.state.csrf_token = fresh
    request.state.csrf_cookie_needs_set = True
    return fresh


def attach_csrf_cookie_if_needed(request: Request, response: Response) -> None:
    """Set the HttpOnly CSRF cookie if a new token was generated for this request."""
    if getattr(request.state, "csrf_cookie_needs_set", False):
        token = getattr(request.state, "csrf_token", None)
        if isinstance(token, str) and _is_valid_token_format(token):
            set_raw_cookie(
                response,
                request,
                name=CSRF_COOKIE_NAME,
                value=token,
                max_age=CSRF_MAX_AGE,
                httponly=True,
                samesite="lax",
            )


async def _extract_submitted_token(request: Request) -> str | None:
    header_token = request.headers.get(CSRF_HEADER_NAME, "").strip()
    if header_token:
        return header_token

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        # request.form() caches parsed form data on Starlette Request, avoiding
        # "Stream consumed" when FastAPI resolves Form(...) parameters.
        form = await request.form()
        val = form.get(CSRF_FORM_FIELD)
        if isinstance(val, str):
            return val.strip()

    return None


async def verify_csrf(request: Request) -> None:
    """Enforce double-submit CSRF on all state-changing methods.

    Bypassed ONLY when environment variable TESTING == "1". NEVER by User-Agent.
    """
    if request.method.upper() in _SAFE_METHODS:
        return

    if is_testing():
        return

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "").strip()
    if not _is_valid_token_format(cookie_token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")

    submitted_token = await _extract_submitted_token(request)
    if submitted_token is None or not _is_valid_token_format(submitted_token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")

    if not hmac.compare_digest(cookie_token, submitted_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch.")
