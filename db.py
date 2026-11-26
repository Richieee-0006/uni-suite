"""SQLite WAL database layer with Argon2id + transparent bcrypt upgrade."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import bcrypt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from webapp.config import get_admin_seed_config, get_db_path

# OWASP-recommended Argon2id parameters
_ARGON2_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# Precomputed Argon2id digest matching _ARGON2_HASHER for constant-time misses.
_TIMING_DUMMY_DIGEST = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "5aiDFe7Yn85V8Q20YSoQLw$ZqDGSYItGS66nOikWcariG+0S3BDEkmPukxPxR0+uMs"
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    session_version INTEGER NOT NULL DEFAULT 1,
    preferences_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS subject_progress (
    user_id INTEGER NOT NULL,
    subject_slug TEXT NOT NULL,
    topic_id TEXT NOT NULL DEFAULT '',
    question_id TEXT NOT NULL,
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    last_correct INTEGER NOT NULL DEFAULT 0,
    stability REAL NOT NULL DEFAULT 0,
    difficulty REAL NOT NULL DEFAULT 5.0,
    due_at TEXT,
    lapses INTEGER NOT NULL DEFAULT 0,
    last_error_reason TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (user_id, subject_slug, question_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_subject_progress_user_slug
    ON subject_progress (user_id, subject_slug);

CREATE INDEX IF NOT EXISTS idx_subject_progress_due
    ON subject_progress (user_id, subject_slug, due_at);

CREATE TABLE IF NOT EXISTS daily_progress (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    answered INTEGER NOT NULL DEFAULT 0,
    correct INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    subject_slug TEXT NOT NULL,
    mode TEXT NOT NULL,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    duration_sec INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_scores_user_slug
    ON user_scores (user_id, subject_slug, created_at);
"""


def _is_supported_hash(stored_hash: str) -> bool:
    return stored_hash.startswith(
        ("$argon2id$", "$argon2i$", "$argon2d$", "$2a$", "$2b$", "$2y$")
    )


def hash_password(plain: str) -> str:
    """Hash a plaintext password using Argon2id."""
    if not isinstance(plain, str) or not plain:
        raise ValueError("Password must be a non-empty string.")
    if len(plain) > MAX_PASSWORD_LEN:
        raise ValueError("Password exceeds maximum allowed length.")
    return _ARGON2_HASHER.hash(plain)


def verify_password(plain: str, stored_hash: str) -> tuple[bool, bool]:
    """Verify password against Argon2id or legacy bcrypt hash.

    Returns (is_valid, needs_upgrade_to_argon2id).
    """
    if not plain or not stored_hash or len(plain) > MAX_PASSWORD_LEN:
        return False, False

    if stored_hash.startswith("$argon2"):
        try:
            _ARGON2_HASHER.verify(stored_hash, plain)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, False
        needs_rehash = not stored_hash.startswith(
            "$argon2id$"
        ) or _ARGON2_HASHER.check_needs_rehash(stored_hash)
        return True, needs_rehash

    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            ok = bcrypt.checkpw(plain.encode("utf-8"), stored_hash.encode("utf-8"))
        except ValueError:
            return False, False
        return (True, True) if ok else (False, False)

    return False, False


def _restrict_file_permissions(path: Path) -> None:
    """Apply owner-only 0600 permissions to SQLite database files on POSIX systems."""
    if os.name != "posix":
        return
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            os.chmod(candidate, 0o600)


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a configured SQLite WAL connection."""
    resolved = db_path if db_path is not None else get_db_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: Path | None = None) -> Path:
    """Initialize schema, enforce 0600 permissions, and seed admin from env only."""
    resolved = db_path if db_path is not None else get_db_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    with get_connection(resolved) as conn:
        conn.executescript(_SCHEMA_SQL)
        admin_seed = get_admin_seed_config()
        if admin_seed is not None:
            admin_username, admin_hash = admin_seed
            if not USERNAME_RE.fullmatch(admin_username):
                raise RuntimeError("ADMIN_USERNAME has an invalid format.")
            if not _is_supported_hash(admin_hash):
                raise RuntimeError(
                    "HASHED_PASSWORD must be an Argon2id or bcrypt hash, not plaintext."
                )
            existing = conn.execute(
                "SELECT id, role FROM users WHERE username = ?",
                (admin_username,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role) "
                    "VALUES (?, ?, 'admin')",
                    (admin_username, admin_hash),
                )
            elif existing["role"] != "admin":
                conn.execute(
                    "UPDATE users SET role = 'admin' WHERE id = ?",
                    (existing["id"],),
                )
        conn.commit()

    _restrict_file_permissions(resolved)
    return resolved


def _row_to_public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "session_version": int(row["session_version"]),
        "preferences_json": str(row["preferences_json"] or "{}"),
        "created_at": str(row["created_at"]),
    }


def create_user(
    username: str,
    password: str,
    *,
    role: str = "user",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Create a new user with an Argon2id password hash."""
    cleaned_user = username.strip()
    if not USERNAME_RE.fullmatch(cleaned_user):
        raise ValueError(
            "Uživatelské jméno musí mít 3–32 znaků (písmena, číslice, _, ., -)."
        )
    if len(password) < MIN_PASSWORD_LEN or len(password) > MAX_PASSWORD_LEN:
        raise ValueError("Heslo musí mít alespoň 8 znaků (maximálně 128).")
    if role not in {"user", "admin"}:
        raise ValueError("Invalid role.")

    pw_hash = hash_password(password)
    with get_connection(db_path) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (cleaned_user, pw_hash, role),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Toto uživatelské jméno už existuje.") from exc
        row = conn.execute(
            "SELECT id, username, role, session_version, preferences_json, created_at "
            "FROM users WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return _row_to_public_user(row)


def get_user_by_id(user_id: int, db_path: Path | None = None) -> dict[str, Any] | None:
    """Fetch a user by numeric ID without returning password_hash."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, role, session_version, preferences_json, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_public_user(row) if row is not None else None


def get_user_by_username(
    username: str, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Fetch a user by username without returning password_hash."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, role, session_version, preferences_json, created_at "
            "FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
    return _row_to_public_user(row) if row is not None else None


def authenticate_user(
    username: str,
    password: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Authenticate user and transparently upgrade legacy bcrypt hashes to Argon2id."""
    cleaned_user = username.strip()
    if not cleaned_user or not password:
        return None

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role, session_version, "
            "preferences_json, created_at FROM users WHERE username = ?",
            (cleaned_user,),
        ).fetchone()
        if row is None:
            # Equalize response timing with a real Argon2id verification pass.
            verify_password(password, _TIMING_DUMMY_DIGEST)
            return None

        valid, needs_rehash = verify_password(password, str(row["password_hash"]))
        if not valid:
            return None

        if needs_rehash:
            upgraded_hash = hash_password(password)
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (upgraded_hash, row["id"]),
            )
            conn.commit()

        return _row_to_public_user(row)


def increment_session_version(user_id: int, db_path: Path | None = None) -> int:
    """Increment session_version to revoke all previously issued sessions."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
            (user_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT session_version FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return int(row["session_version"]) if row is not None else 0


def get_user_preferences_json(user_id: int, db_path: Path | None = None) -> str:
    """Return raw preferences_json string stored for user_id."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT preferences_json FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return str(row["preferences_json"] or "{}") if row is not None else "{}"


def save_user_preferences_json(
    user_id: int,
    preferences_json: str,
    db_path: Path | None = None,
) -> None:
    """Persist validated preferences JSON string for user_id."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE users SET preferences_json = ? WHERE id = ?",
            (preferences_json, user_id),
        )
        conn.commit()


def get_user_subject_progress(
    user_id: int,
    subject_slug: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return subject_progress rows for user_id, optionally filtered by slug."""
    with get_connection(db_path) as conn:
        if subject_slug is not None:
            rows = conn.execute(
                "SELECT subject_slug, topic_id, question_id, correct_count, "
                "wrong_count, last_correct, stability, difficulty, due_at, "
                "lapses, last_error_reason, updated_at "
                "FROM subject_progress WHERE user_id = ? AND subject_slug = ?",
                (user_id, subject_slug),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT subject_slug, topic_id, question_id, correct_count, "
                "wrong_count, last_correct, stability, difficulty, due_at, "
                "lapses, last_error_reason, updated_at "
                "FROM subject_progress WHERE user_id = ?",
                (user_id,),
            ).fetchall()
    return [dict(row) for row in rows]


def _compute_streak(active_days: set[str], today_date: date) -> int:
    streak = 0
    cursor = today_date
    if cursor.isoformat() not in active_days:
        cursor = today_date - timedelta(days=1)
    while cursor.isoformat() in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def get_daily_summary(
    user_id: int | None,
    *,
    daily_goal: int = 10,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Return {done, goal, streak, weak_count} for the portal and sidebar."""
    if user_id is None:
        return {"done": 0, "goal": daily_goal, "streak": 0, "weak_count": 0}

    today_date = datetime.now(timezone.utc).date()
    today_iso = today_date.isoformat()

    with get_connection(db_path) as conn:
        today_row = conn.execute(
            "SELECT answered FROM daily_progress WHERE user_id = ? AND day = ?",
            (user_id, today_iso),
        ).fetchone()
        done = int(today_row["answered"]) if today_row is not None else 0

        day_rows = conn.execute(
            "SELECT day FROM daily_progress WHERE user_id = ? AND answered > 0 "
            "ORDER BY day DESC LIMIT 365",
            (user_id,),
        ).fetchall()
        active_days = {str(r["day"]) for r in day_rows}

        weak_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM subject_progress "
            "WHERE user_id = ? "
            "AND (lapses >= 1 OR (wrong_count > 0 AND last_correct = 0))",
            (user_id,),
        ).fetchone()
        weak_count = int(weak_row["cnt"]) if weak_row is not None else 0

    return {
        "done": done,
        "goal": daily_goal,
        "streak": _compute_streak(active_days, today_date),
        "weak_count": weak_count,
    }


def get_subject_error_and_due_counts(
    user_id: int | None,
    subject_slug: str,
    db_path: Path | None = None,
) -> tuple[int, int]:
    """Return (error_count, due_count) for a specific user and subject."""
    if user_id is None:
        return 0, 0

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection(db_path) as conn:
        err_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM subject_progress "
            "WHERE user_id = ? AND subject_slug = ? "
            "AND (lapses >= 1 OR (wrong_count > 0 AND last_correct = 0))",
            (user_id, subject_slug),
        ).fetchone()
        due_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM subject_progress "
            "WHERE user_id = ? AND subject_slug = ? "
            "AND due_at IS NOT NULL AND due_at <= ?",
            (user_id, subject_slug, now_iso),
        ).fetchone()

    return (
        int(err_row["cnt"]) if err_row is not None else 0,
        int(due_row["cnt"]) if due_row is not None else 0,
    )


def get_admin_stats(db_path: Path | None = None) -> dict[str, int]:
    """Return aggregate statistics for the isolated admin panel."""
    today_iso = datetime.now(timezone.utc).date().isoformat()
    with get_connection(db_path) as conn:
        users_total = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        admins_total = int(
            conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[
                0
            ]
        )
        progress_rows = int(
            conn.execute("SELECT COUNT(*) FROM subject_progress").fetchone()[0]
        )
        scores_total = int(
            conn.execute("SELECT COUNT(*) FROM user_scores").fetchone()[0]
        )
        daily_active_today = int(
            conn.execute(
                "SELECT COUNT(*) FROM daily_progress WHERE day = ? AND answered > 0",
                (today_iso,),
            ).fetchone()[0]
        )
    return {
        "users": users_total,
        "users_total": users_total,
        "admins_total": admins_total,
        "progress_rows": progress_rows,
        "scores_total": scores_total,
        "daily_active_today": daily_active_today,
    }
