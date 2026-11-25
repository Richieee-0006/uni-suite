"""Tests for Phase 3 TZI migration: assets, internal loopback bundle, and DB tool."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from scripts.migrate_tzi_db import IGNORED_PVP_TABLES, migrate_tzi_db
from scripts.sync_tzi_upstream import (
    scan_personal_path_leaks,
    sync_from_directory,
    validate_bank_payload,
    validate_subject_tree,
)
from webapp import subjects as subjects_mod
from webapp.routers import internal as internal_router

REPO_ROOT = Path(__file__).resolve().parent.parent
TZI_DIR = REPO_ROOT / "subjects" / "tzi"


# ---------------------------------------------------------------------------
# 1. subjects/tzi/ integrity & upstream sync validator tests
# ---------------------------------------------------------------------------


def test_tzi_meta_schema_v2() -> None:
    meta = json.loads((TZI_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["slug"] == "tzi"
    assert meta["code"] == "KI/TIN"
    assert meta["short"] == "TZI"
    assert meta["visibility"] == "public"
    assert meta["provider"] == {
        "type": "upstream_git",
        "repo": "zdenax/TZI-TIN-APP",
    }
    assert meta["engines"] == ["choice", "input_exact", "recall", "composite_tool"]
    assert meta["tools"] == ["glossary", "automata"]
    assert meta["gamification"] == {"xp": True, "daily_goal": True}
    assert meta["exam_config"] == {"count": 15, "minutes": 30, "pass_pct": 60}


def test_tzi_subject_tree_valid_and_clean() -> None:
    errors = validate_subject_tree(TZI_DIR)
    assert errors == []

    bank_files = sorted((TZI_DIR / "banky").glob("*.json"))
    recall_files = sorted((TZI_DIR / "banky" / "doplnovacka").glob("*.json"))
    assert len(bank_files) >= 11
    assert len(recall_files) >= 1
    assert (TZI_DIR / "glosar" / "glosar.json").is_file()
    assert (TZI_DIR / "glosar" / "doplnky.json").is_file()

    # Every JSON file in subjects/tzi must parse cleanly and contain zero path leaks
    all_json = sorted(TZI_DIR.rglob("*.json"))
    for path in all_json:
        raw = path.read_text(encoding="utf-8")
        json.loads(raw)
        assert scan_personal_path_leaks(raw, str(path.name)) == []


def test_tzi_formulas_quick_reference() -> None:
    raw = (TZI_DIR / "formulas.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    sections = payload["sections"]
    assert len(sections) >= 5

    # Verify webapp/subjects.py formula scanner parses it cleanly
    scanned = subjects_mod._scan_formulas(TZI_DIR)
    assert len(scanned) >= 15
    joined = " ".join(item["latex"] + " " + item["name"] for item in scanned)
    assert "M = (Q, \\Sigma, \\delta, q_0, F)" in joined
    assert "\\binom{n}{k}" in joined
    assert "\\Sigma^*" in joined
    assert "\\mathcal{P}(A)" in joined


def test_sync_tzi_upstream_validation_and_dry_run(tmp_path: Path) -> None:
    bad_errors = validate_bank_payload(
        {"title": "", "questions": [{"id": "q1", "type": "choice", "text": "x"}]},
        "broken.json",
    )
    assert len(bad_errors) >= 2

    leak_sample = '{"note": "' + "/home/exampleuser/private" + '"}'
    assert len(scan_personal_path_leaks(leak_sample, "leak.json")) == 1

    # Sync from clean TZI_DIR into fresh target with dry-run first, then real sync
    target = tmp_path / "tzi_copy"
    dry_report = sync_from_directory(TZI_DIR, target, dry_run=True)
    assert dry_report["dry_run"] is True
    assert dry_report["total_files"] >= 14
    assert not target.exists()

    real_report = sync_from_directory(TZI_DIR, target, dry_run=False)
    assert real_report["dry_run"] is False
    assert len(real_report["added"]) >= 14
    assert (target / "banky" / "01-2026-10-01.json").is_file()
    assert (target / "banky" / "doplnovacka" / "04r-doplnovacka.json").is_file()
    assert (target / "glosar" / "glosar.json").is_file()

    # Second run should report all unchanged
    repeat_report = sync_from_directory(TZI_DIR, target, dry_run=False)
    assert repeat_report["added"] == []
    assert repeat_report["updated"] == []
    assert len(repeat_report["unchanged"]) == real_report["total_files"]


# ---------------------------------------------------------------------------
# 2. GET /internal/subjects/{slug}/bundle security & payload tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def internal_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(subjects_mod, "SUBJECTS_DIR", REPO_ROOT / "subjects")
    monkeypatch.setenv("TESTING", "1")
    app = FastAPI()
    app.include_router(internal_router.router)
    return TestClient(app)


def test_internal_bundle_404_without_configured_token(
    internal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTERNAL_SYNC_TOKEN", raising=False)
    r1 = internal_client.get("/internal/subjects/tzi/bundle")
    r2 = internal_client.get(
        "/internal/subjects/tzi/bundle",
        headers={"Authorization": "Bearer anything"},
    )
    assert r1.status_code == 404
    assert r2.status_code == 404


def test_internal_bundle_404_missing_or_wrong_token(
    internal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_SYNC_TOKEN", "sync-token-for-unit-test-only-123456")
    r_missing = internal_client.get("/internal/subjects/tzi/bundle")
    r_wrong_bearer = internal_client.get(
        "/internal/subjects/tzi/bundle",
        headers={"Authorization": "Bearer wrong-token"},
    )
    r_wrong_header = internal_client.get(
        "/internal/subjects/tzi/bundle",
        headers={"X-Internal-Token": "wrong-token"},
    )
    assert r_missing.status_code == 404
    assert r_wrong_bearer.status_code == 404
    assert r_wrong_header.status_code == 404


def test_internal_bundle_404_when_proxied_headers_present(
    internal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "sync-token-for-unit-test-only-123456"
    monkeypatch.setenv("INTERNAL_SYNC_TOKEN", token)
    r_cf = internal_client.get(
        "/internal/subjects/tzi/bundle",
        headers={
            "Authorization": f"Bearer {token}",
            "CF-Connecting-IP": "198.51.100.10",
        },
    )
    r_xff = internal_client.get(
        "/internal/subjects/tzi/bundle",
        headers={
            "X-Internal-Token": token,
            "X-Forwarded-For": "203.0.113.5",
        },
    )
    assert r_cf.status_code == 404
    assert r_xff.status_code == 404


def test_internal_bundle_404_testclient_blocked_when_not_testing(
    internal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "sync-token-for-unit-test-only-123456"
    monkeypatch.setenv("INTERNAL_SYNC_TOKEN", token)
    monkeypatch.setenv("TESTING", "0")
    resp = internal_client.get(
        "/internal/subjects/tzi/bundle",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_internal_bundle_valid_loopback_returns_full_bundle_and_sha256(
    internal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "sync-token-for-unit-test-only-123456"
    monkeypatch.setenv("INTERNAL_SYNC_TOKEN", token)

    resp_bearer = internal_client.get(
        "/internal/subjects/tzi/bundle",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_bearer.status_code == 200
    assert resp_bearer.headers.get("cache-control") == "no-store"

    data = resp_bearer.json()
    assert set(data.keys()) == {
        "slug",
        "meta",
        "banks",
        "recall_banks",
        "glossary",
        "formulas",
        "sha256",
    }
    assert data["slug"] == "tzi"
    assert data["meta"]["code"] == "KI/TIN"
    assert "01-2026-10-01" in data["banks"]
    assert "04r-doplnovacka" in data["recall_banks"]
    assert "01-2026-10-01" in data["glossary"]["banks"]
    assert len(data["formulas"]["sections"]) >= 5

    core = {k: data[k] for k in data if k != "sha256"}
    assert data["sha256"] == internal_router.compute_bundle_sha256(core)

    # X-Internal-Token header should return the exact same bundle and digest
    resp_alt = internal_client.get(
        "/internal/subjects/tzi/bundle",
        headers={"X-Internal-Token": token},
    )
    assert resp_alt.status_code == 200
    assert resp_alt.json()["sha256"] == data["sha256"]

    # Non-existent or invalid slug still returns 404
    assert (
        internal_client.get(
            "/internal/subjects/unknown_subj/bundle",
            headers={"X-Internal-Token": token},
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# 3. scripts/migrate_tzi_db.py end-to-end tests
# ---------------------------------------------------------------------------


def _build_synthetic_tzi_db(db_path: Path, student_pw: str, admin_pw: str) -> None:
    student_hash = bcrypt.hashpw(student_pw.encode("utf-8"), bcrypt.gensalt(4)).decode(
        "utf-8"
    )
    admin_hash = bcrypt.hashpw(admin_pw.encode("utf-8"), bcrypt.gensalt(4)).decode(
        "utf-8"
    )

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            theme TEXT NOT NULL DEFAULT 'games',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE user_progress (
            user_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            seen_json TEXT NOT NULL DEFAULT '{}',
            session_json TEXT DEFAULT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, source)
        );
        CREATE TABLE user_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question_id TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            attempt_number INTEGER NOT NULL DEFAULT 1,
            points_awarded INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE daily_progress (
            user_id INTEGER NOT NULL,
            activity_date TEXT NOT NULL,
            questions_answered INTEGER NOT NULL DEFAULT 0,
            questions_correct INTEGER NOT NULL DEFAULT 0,
            goal_met INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, activity_date)
        );
        CREATE TABLE user_scores (
            user_id INTEGER PRIMARY KEY,
            total_points INTEGER NOT NULL DEFAULT 0,
            current_streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            daily_streak INTEGER NOT NULL DEFAULT 0,
            best_daily_streak INTEGER NOT NULL DEFAULT 0,
            last_goal_date TEXT DEFAULT NULL,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE koth_state (
            id INTEGER PRIMARY KEY,
            king_username TEXT,
            king_score INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE koth_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenger_username TEXT NOT NULL,
            challenger_score INTEGER NOT NULL
        );
        CREATE TABLE ffa_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            score INTEGER NOT NULL
        );
        CREATE TABLE duel_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE TABLE comp_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE comp_room_players (
            room_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (room_id, user_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (1, 'student_alice', ?, 'student', '2026-10-05 10:00:00')",
        (student_hash,),
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (2, 'boss_admin', ?, 'admin', '2026-10-01 09:00:00')",
        (admin_hash,),
    )
    conn.execute(
        "INSERT INTO user_progress (user_id, source, seen_json, updated_at) "
        "VALUES (1, '01-2026-10-01', ?, '2026-10-06 12:00:00')",
        (json.dumps({"1-1": "correct", "1-2": "wrong"}),),
    )
    conn.execute(
        "INSERT INTO user_answers (user_id, question_id, is_correct, created_at) "
        "VALUES (1, '1-1', 0, '2026-10-05 11:00:00'), "
        "(1, '1-1', 1, '2026-10-06 11:00:00'), "
        "(1, '1-3', 1, '2026-10-06 11:05:00')"
    )
    conn.execute(
        "INSERT INTO daily_progress "
        "(user_id, activity_date, questions_answered, questions_correct, goal_met) "
        "VALUES (1, '2026-10-06', 12, 10, 1)"
    )
    conn.execute(
        "INSERT INTO user_scores "
        "(user_id, total_points, current_streak, best_streak, last_active) "
        "VALUES (1, 450, 4, 9, '2026-10-06 12:30:00')"
    )
    # Seed PvP/Arena rows that MUST NEVER be migrated
    conn.execute("INSERT INTO koth_state VALUES (1, 'student_alice', 999)")
    conn.execute("INSERT INTO koth_matches (challenger_username, challenger_score) "
                 "VALUES ('student_alice', 999)")
    conn.execute("INSERT INTO ffa_runs (user_id, score) VALUES (1, 777)")
    conn.execute("INSERT INTO duel_matches (creator_id, status) VALUES (1, 'closed')")
    conn.execute("INSERT INTO comp_rooms (id, name) VALUES (1, 'Arena 1')")
    conn.execute("INSERT INTO comp_room_players (room_id, user_id) VALUES (1, 1)")
    conn.commit()
    conn.close()


def test_migrate_tzi_db_end_to_end_idempotent_and_no_pvp(tmp_path: Path) -> None:
    src_db = tmp_path / "tzi.db"
    dst_db = tmp_path / "uni.db"
    student_pw = "CorrectHorseBattery99!"
    admin_pw = "AdminSecurePassword42!"

    _build_synthetic_tzi_db(src_db, student_pw, admin_pw)

    # 1. Dry-run must NOT create destination DB
    dry_report = migrate_tzi_db(
        src_db,
        dst_db,
        dry_run=True,
        subjects_dir=REPO_ROOT / "subjects",
    )
    assert dry_report["dry_run"] is True
    assert dry_report["users_created"] == 2
    assert dry_report["progress_migrated"] == 3
    assert dry_report["daily_migrated"] == 1
    assert dry_report["scores_migrated"] == 1
    assert set(dry_report["ignored_pvp_tables"]) == IGNORED_PVP_TABLES
    assert not dst_db.exists()

    # 2. Real migration
    report = migrate_tzi_db(
        src_db,
        dst_db,
        dry_run=False,
        subjects_dir=REPO_ROOT / "subjects",
    )
    assert report["users_created"] == 2
    assert report["users_existing"] == 0
    assert report["progress_migrated"] == 3
    assert report["daily_migrated"] == 1
    assert report["scores_migrated"] == 1
    assert set(report["ignored_pvp_tables"]) == IGNORED_PVP_TABLES

    if os.name == "posix":
        mode = stat.S_IMODE(dst_db.stat().st_mode)
        assert mode == 0o600

    with db.get_connection(dst_db) as conn:
        dst_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        # Zero PvP/Arena tables in destination
        assert dst_tables.isdisjoint(IGNORED_PVP_TABLES)
        assert dst_tables == {
            "users",
            "subject_progress",
            "daily_progress",
            "user_scores",
        }

        alice = conn.execute(
            "SELECT id, role, password_hash FROM users WHERE username = 'student_alice'"
        ).fetchone()
        boss = conn.execute(
            "SELECT id, role, password_hash FROM users WHERE username = 'boss_admin'"
        ).fetchone()
        assert alice["role"] == "user"
        assert boss["role"] == "admin"
        # Existing bcrypt hashes preserved verbatim
        assert str(alice["password_hash"]).startswith("$2b$")
        assert str(boss["password_hash"]).startswith("$2b$")

        prog_rows = {
            r["question_id"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM subject_progress WHERE user_id = ?",
                (alice["id"],),
            ).fetchall()
        }
        assert set(prog_rows.keys()) == {"1-1", "1-2", "1-3"}
        assert all(r["subject_slug"] == "tzi" for r in prog_rows.values())
        assert prog_rows["1-1"]["correct_count"] == 1
        assert prog_rows["1-1"]["wrong_count"] == 1
        assert prog_rows["1-1"]["last_correct"] == 1
        assert prog_rows["1-1"]["topic_id"] == "01-2026-10-01"
        assert prog_rows["1-2"]["wrong_count"] == 1
        assert prog_rows["1-2"]["last_correct"] == 0
        assert prog_rows["1-3"]["topic_id"] == "01-2026-10-01"

        daily = conn.execute(
            "SELECT * FROM daily_progress WHERE user_id = ? AND day = '2026-10-06'",
            (alice["id"],),
        ).fetchone()
        assert daily["answered"] == 12
        assert daily["correct"] == 10

        scores = conn.execute(
            "SELECT * FROM user_scores WHERE user_id = ?",
            (alice["id"],),
        ).fetchall()
        assert len(scores) == 1
        assert scores[0]["subject_slug"] == "tzi"
        assert scores[0]["mode"] == "legacy_tzi"
        assert scores[0]["score"] == 450

    # 3. Login transparently upgrades preserved bcrypt hash to Argon2id
    authed = db.authenticate_user("student_alice", student_pw, db_path=dst_db)
    assert authed is not None
    assert authed["username"] == "student_alice"
    with db.get_connection(dst_db) as conn:
        upgraded = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'student_alice'"
        ).fetchone()["password_hash"]
        assert str(upgraded).startswith("$argon2id$")

    # 4. Re-running migration is strictly idempotent and keeps upgraded Argon2id hash
    second = migrate_tzi_db(
        src_db,
        dst_db,
        dry_run=False,
        subjects_dir=REPO_ROOT / "subjects",
    )
    assert second["users_created"] == 0
    assert second["users_existing"] == 2
    assert second["scores_migrated"] == 0
    assert second["scores_skipped"] == 1

    with db.get_connection(dst_db) as conn:
        still_argon = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'student_alice'"
        ).fetchone()["password_hash"]
        assert str(still_argon).startswith("$argon2id$")
        assert conn.execute("SELECT COUNT(*) FROM user_scores").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT answered FROM daily_progress WHERE day = '2026-10-06'"
            ).fetchone()[0]
            == 12
        )
        assert (
            conn.execute(
                "SELECT correct_count FROM subject_progress WHERE question_id = '1-1'"
            ).fetchone()[0]
            == 1
        )


def test_migrate_tzi_db_supports_flat_progress_and_scores_tables(
    tmp_path: Path,
) -> None:
    src_db = tmp_path / "flat_tzi.db"
    dst_db = tmp_path / "flat_uni.db"
    pw_hash = bcrypt.hashpw(b"FlatUserPass99!", bcrypt.gensalt(4)).decode("utf-8")

    conn = sqlite3.connect(str(src_db))
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
        );
        CREATE TABLE progress (
            user_id INTEGER NOT NULL,
            question_id TEXT NOT NULL,
            correct_count INTEGER NOT NULL,
            wrong_count INTEGER NOT NULL,
            last_correct INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE daily_progress (
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            answered INTEGER NOT NULL,
            correct INTEGER NOT NULL
        );
        CREATE TABLE scores (
            user_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            duration_sec INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO users VALUES (10, 'bob_flat', ?, 'student')",
        (pw_hash,),
    )
    conn.execute(
        "INSERT INTO progress VALUES (10, '1-1', 3, 1, 1, '2026-11-01T10:00:00Z')"
    )
    conn.execute("INSERT INTO daily_progress VALUES (10, '2026-11-01', 5, 4)")
    conn.execute(
        "INSERT INTO scores VALUES (10, 'exam', 13, 15, 620, '2026-11-01T10:30:00Z')"
    )
    conn.commit()
    conn.close()

    r1 = migrate_tzi_db(src_db, dst_db, subjects_dir=REPO_ROOT / "subjects")
    r2 = migrate_tzi_db(src_db, dst_db, subjects_dir=REPO_ROOT / "subjects")
    assert r1["users_created"] == 1
    assert r1["progress_migrated"] == 1
    assert r1["daily_migrated"] == 1
    assert r1["scores_migrated"] == 1
    assert r2["scores_skipped"] == 1

    with db.get_connection(dst_db) as dst:
        score = dst.execute("SELECT * FROM user_scores").fetchone()
        assert score["subject_slug"] == "tzi"
        assert score["mode"] == "exam"
        assert score["score"] == 13
        assert score["total"] == 15
        assert score["duration_sec"] == 620
