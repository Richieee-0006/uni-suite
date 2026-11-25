"""Session management with IP/UA binding, session_version checks, and 404 guard."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from fastapi import HTTPException, Request, Response

import db
from webapp.config import (
    SESSION_MAX_AGE,
    TRIAL_MAX_AGE,
    get_app_secret,
    trust_proxy_headers,
)
from webapp.cookie_utils import clear_cookie, set_signed_cookie, unsign_value

SESSION_COOKIE_NAME = "uni_session"
SESSION_COOKIE_SALT = "uni-session-v1"
TRIAL_COOKIE_NAME = "uni_trial"
TRIAL_COOKIE_SALT = "uni-trial-v1"


def get_client_ip(request: Request) -> str:
    """Extract normalized client IP for session binding."""
    if trust_proxy_headers():
        cf_ip = request.headers.get("cf-connecting-ip", "").strip()
        if cf_ip:
            return cf_ip[:64]
        xff = request.headers.get("x-forwarded-for", "").strip()
        if xff:
            first_ip = xff.split(",")[0].strip()
            if first_ip:
                return first_ip[:64]
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return "unknown"


def get_client_ua(request: Request) -> str:
    """Extract normalized client User-Agent for session binding."""
    return request.headers.get("user-agent", "").strip()[:256]


def compute_client_fingerprint(request: Request) -> str:
    """Compute keyed HMAC-SHA256 binding of client IP + User-Agent."""
    secret = get_app_secret().encode("utf-8")
    raw = f"{get_client_ip(request)}|{get_client_ua(request)}".encode("utf-8")
    return hmac.new(secret, raw, hashlib.sha256).hexdigest()[:32]


def create_user_session(
    response: Response, request: Request, user: dict[str, Any]
) -> None:
    """Issue a signed session cookie bound to client IP/UA and DB session_version."""
    payload = {
        "uid": int(user["id"]),
        "sv": int(user["session_version"]),
        "fp": compute_client_fingerprint(request),
    }
    set_signed_cookie(
        response,
        request,
        name=SESSION_COOKIE_NAME,
        data=payload,
        salt=SESSION_COOKIE_SALT,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    # Clear trial cookie if upgrading from trial to full session.
    clear_cookie(response, request, name=TRIAL_COOKIE_NAME)


def create_trial_session(response: Response, request: Request) -> None:
    """Issue a lightweight signed Trial cookie without writing to SQLite."""
    payload = {
        "trial": True,
        "fp": compute_client_fingerprint(request),
    }
    set_signed_cookie(
        response,
        request,
        name=TRIAL_COOKIE_NAME,
        data=payload,
        salt=TRIAL_COOKIE_SALT,
        max_age=TRIAL_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def clear_all_sessions(response: Response, request: Request) -> None:
    """Remove both authenticated session and trial cookies."""
    clear_cookie(response, request, name=SESSION_COOKIE_NAME)
    clear_cookie(response, request, name=TRIAL_COOKIE_NAME)


def get_current_user(request: Request) -> dict[str, Any] | None:
    """Resolve user from signed session cookie (DB + IP/UA checked) or Trial cookie."""
    expected_fp = compute_client_fingerprint(request)

    raw_session = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_session:
        data = unsign_value(
            raw_session, salt=SESSION_COOKIE_SALT, max_age=SESSION_MAX_AGE
        )
        if isinstance(data, dict):
            uid = data.get("uid")
            sv = data.get("sv")
            fp = data.get("fp")
            if (
                isinstance(uid, int)
                and not isinstance(uid, bool)
                and isinstance(sv, int)
                and not isinstance(sv, bool)
                and isinstance(fp, str)
                and hmac.compare_digest(fp, expected_fp)
            ):
                user = db.get_user_by_id(uid)
                if user is not None and int(user["session_version"]) == sv:
                    return {
                        "id": user["id"],
                        "username": user["username"],
                        "role": user["role"],
                        "session_version": user["session_version"],
                        "is_authenticated": True,
                        "is_trial": False,
                    }
        # Invalid/revoked/mismatched session cookie never silently degrades to trial.
        return None

    raw_trial = request.cookies.get(TRIAL_COOKIE_NAME)
    if raw_trial:
        trial_data = unsign_value(
            raw_trial, salt=TRIAL_COOKIE_SALT, max_age=TRIAL_MAX_AGE
        )
        if (
            isinstance(trial_data, dict)
            and trial_data.get("trial") is True
            and isinstance(trial_data.get("fp"), str)
            and hmac.compare_digest(trial_data["fp"], expected_fp)
        ):
            return {
                "id": None,
                "username": "Host (Trial)",
                "role": "trial",
                "is_authenticated": False,
                "is_trial": True,
            }

    return None


def is_admin_user(user: dict[str, Any] | None) -> bool:
    """Return True strictly for an authenticated admin user."""
    return bool(user and user.get("is_authenticated") and user.get("role") == "admin")


def require_admin_404(request: Request) -> dict[str, Any]:
    """Anti-enumeration admin guard: ALWAYS raises 404 Not Found for non-admins."""
    user = get_current_user(request)
    if user is None or not is_admin_user(user):
        raise HTTPException(status_code=404, detail="Not Found")
    return user
