"""Fail-fast configuration loader for UNI Suite."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR: Path = Path(__file__).resolve().parent.parent
SUBJECTS_DIR: Path = BASE_DIR / "subjects"
UI_DIR: Path = BASE_DIR / "ui"
TEMPLATES_DIR: Path = UI_DIR / "templates"
ADMIN_UI_DIR: Path = UI_DIR / "admin"
PUBLIC_DIR: Path = UI_DIR / "public"

# Load .env without overriding process env vars (needed for tests and containers).
load_dotenv(BASE_DIR / ".env", override=False)

MIN_SECRET_LENGTH = 32
MIN_SECRET_UNIQUE_CHARS = 8
_WEAK_PREFIXES = ("change-me", "changeme", "replace-me", "placeholder", "dev-secret")
_WEAK_EXACT = frozenset({"secret", "default", "development", "testing", "uni-suite"})

SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 days
TRIAL_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
CSRF_MAX_AGE = 60 * 60 * 24 * 14  # 14 days

_EPHEMERAL_TEST_SECRET: str | None = None


def is_testing() -> bool:
    """Return True strictly when TESTING=1 is set in the environment."""
    return os.getenv("TESTING") == "1"


def validate_app_secret(raw: str | None, *, testing: bool | None = None) -> str:
    """Validate APP_SECRET fail-fast unless TESTING=1 allows an ephemeral secret."""
    global _EPHEMERAL_TEST_SECRET
    use_testing = is_testing() if testing is None else testing
    cleaned = (raw or "").strip()

    if not cleaned:
        if use_testing:
            if _EPHEMERAL_TEST_SECRET is None:
                _EPHEMERAL_TEST_SECRET = secrets.token_hex(32)
            return _EPHEMERAL_TEST_SECRET
        raise RuntimeError(
            "APP_SECRET is required and must be a random string "
            "of at least 32 characters."
        )

    lowered = cleaned.lower()
    is_weak = (
        len(cleaned) < MIN_SECRET_LENGTH
        or len(set(cleaned)) < MIN_SECRET_UNIQUE_CHARS
        or lowered in _WEAK_EXACT
        or any(lowered.startswith(prefix) for prefix in _WEAK_PREFIXES)
    )
    if is_weak:
        if use_testing:
            if _EPHEMERAL_TEST_SECRET is None:
                _EPHEMERAL_TEST_SECRET = secrets.token_hex(32)
            return _EPHEMERAL_TEST_SECRET
        raise RuntimeError(
            "APP_SECRET is too weak or uses a placeholder value. "
            "Generate a strong random secret (e.g. secrets.token_hex(32))."
        )

    return cleaned


def get_app_secret() -> str:
    """Return the validated application signing secret."""
    return validate_app_secret(os.getenv("APP_SECRET"))


def get_db_path() -> Path:
    """Return resolved SQLite database file path."""
    raw = os.getenv("UNI_DB_PATH", "uni.db").strip() or "uni.db"
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def get_admin_seed_config() -> tuple[str, str] | None:
    """Return (username, hash) ONLY if both ADMIN_USERNAME and HASHED_PASSWORD exist."""
    username = os.getenv("ADMIN_USERNAME", "").strip()
    hashed = os.getenv("HASHED_PASSWORD", "").strip()
    if username and hashed:
        return username, hashed
    return None


def get_github_issues_config() -> tuple[str, str] | None:
    """Return (token, owner/repo) when GitHub Issues feedback is configured."""
    token = os.getenv("GITHUB_ISSUES_TOKEN", "").strip()
    repo = os.getenv("GITHUB_ISSUES_REPO", "Richieee-0006/uni-suite").strip()
    if not token or not repo:
        return None
    parts = repo.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if not all(set(part) <= allowed for part in parts):
        return None
    return token, repo


def cookie_secure_forced() -> bool:
    """Return True if COOKIE_SECURE=1 forces Secure flag even over loopback HTTP."""
    return os.getenv("COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}


def trust_proxy_headers() -> bool:
    """Return True when X-Forwarded-* / CF-Connecting-IP headers should be trusted."""
    raw = os.getenv("TRUST_PROXY_HEADERS", "1").strip().lower()
    return raw not in {"0", "false", "no"}


def get_bind_address() -> tuple[str, int]:
    """Return (host, port) for uvicorn."""
    host = os.getenv("UNI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    raw_port = os.getenv("UNI_PORT", "5052").strip() or "5052"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"Invalid UNI_PORT value: {raw_port!r}") from exc
    if not (1 <= port <= 65535):
        raise RuntimeError(f"UNI_PORT out of range: {port}")
    return host, port


@dataclass(frozen=True)
class Settings:
    """Snapshot of validated runtime configuration."""

    app_secret: str
    db_path: Path
    host: str
    port: int
    cookie_secure: bool
    trust_proxy: bool
    testing: bool
    admin_seed: tuple[str, str] | None


def load_settings() -> Settings:
    """Load and validate all runtime settings (fail-fast)."""
    host, port = get_bind_address()
    return Settings(
        app_secret=get_app_secret(),
        db_path=get_db_path(),
        host=host,
        port=port,
        cookie_secure=cookie_secure_forced(),
        trust_proxy=trust_proxy_headers(),
        testing=is_testing(),
        admin_seed=get_admin_seed_config(),
    )
