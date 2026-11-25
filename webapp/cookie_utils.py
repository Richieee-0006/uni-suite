"""Signed cookie utilities using itsdangerous URLSafeTimedSerializer."""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from itsdangerous import BadData, BadSignature, SignatureExpired, URLSafeTimedSerializer

from webapp.config import cookie_secure_forced, get_app_secret, trust_proxy_headers


def get_serializer(salt: str) -> URLSafeTimedSerializer:
    """Create a timed serializer bound to the validated APP_SECRET and salt."""
    return URLSafeTimedSerializer(secret_key=get_app_secret(), salt=salt)


def sign_value(data: Any, *, salt: str) -> str:
    """Serialize and cryptographically sign a JSON-serializable payload."""
    return get_serializer(salt).dumps(data)


def unsign_value(token: str | None, *, salt: str, max_age: int) -> Any | None:
    """Verify signature and age; return decoded payload or None on any error."""
    if not token:
        return None
    try:
        return get_serializer(salt).loads(token, max_age=max_age)
    except (SignatureExpired, BadSignature, BadData):
        return None


def is_https_request(request: Request) -> bool:
    """Detect whether the request arrived over HTTPS (directly or via proxy)."""
    if request.url.scheme == "https":
        return True
    if trust_proxy_headers():
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        first_proto = forwarded_proto.split(",")[0].strip().lower()
        if first_proto == "https":
            return True
    return False


def is_cookie_secure(request: Request) -> bool:
    """Return True when cookies must carry the Secure attribute."""
    return cookie_secure_forced() or is_https_request(request)


def set_signed_cookie(
    response: Response,
    request: Request,
    *,
    name: str,
    data: Any,
    salt: str,
    max_age: int,
    httponly: bool = True,
    samesite: str = "lax",
    path: str = "/",
) -> None:
    """Attach signed HttpOnly SameSite=Lax cookie (Secure if HTTPS/COOKIE_SECURE=1)."""
    signed = sign_value(data, salt=salt)
    response.set_cookie(
        key=name,
        value=signed,
        max_age=max_age,
        httponly=httponly,
        samesite=samesite,
        secure=is_cookie_secure(request),
        path=path,
    )


def set_raw_cookie(
    response: Response,
    request: Request,
    *,
    name: str,
    value: str,
    max_age: int,
    httponly: bool = True,
    samesite: str = "lax",
    path: str = "/",
) -> None:
    """Attach a cookie with standardized security flags."""
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=httponly,
        samesite=samesite,
        secure=is_cookie_secure(request),
        path=path,
    )


def clear_cookie(
    response: Response,
    request: Request,
    *,
    name: str,
    path: str = "/",
) -> None:
    """Expire a cookie with matching security attributes."""
    response.delete_cookie(
        key=name,
        path=path,
        httponly=True,
        samesite="lax",
        secure=is_cookie_secure(request),
    )
