"""Comprehensive Phase 1 backend & security gate test suite for UNI Suite."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

import bcrypt
import pytest
from fastapi.testclient import TestClient

import db
from webapp.config import load_settings, validate_app_secret
from webapp.csrf import CSRF_COOKIE_NAME
from webapp.main import create_app
from webapp.preferences import (
    DEFAULT_PREFERENCES,
    prefs_to_html_attrs,
    sanitize_preferences,
    validate_preferences_payload,
)
from webapp.routers.auth import validate_next_url
from webapp.subjects import compute_mastery


def _get_csrf(client: TestClient, path: str = "/login") -> str:
    resp = client.get(path)
    assert resp.status_code == 200
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token and len(token) >= 32
    return token


# ---------------------------------------------------------------------------
# 1. Config fail-fast, no default admin, DB WAL & chmod 0600, no PvP tables
# ---------------------------------------------------------------------------


def test_app_secret_fail_fast_when_not_testing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("APP_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        load_settings()

    for weak in ("", "short", "change-me-to-a-long-random-secret", "a" * 64, "secret"):
        with pytest.raises(RuntimeError):
            validate_app_secret(weak, testing=False)

    # Strong secret succeeds outside testing
    strong = "0123456789abcdef0123456789abcdef0123456789abcdef"
    assert validate_app_secret(strong, testing=False) == strong

    # TESTING=1 allows ephemeral secret fallback
    ephemeral = validate_app_secret("", testing=True)
    assert len(ephemeral) >= 32


def test_db_wal_chmod_0600_and_no_pvp_tables(isolated_env: dict[str, Any]) -> None:
    db_path: Path = isolated_env["db_path"]
    mode = stat.S_IMODE(os.stat(db_path).st_mode)
    assert mode == 0o600

    with sqlite3.connect(str(db_path)) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"users", "subject_progress", "daily_progress", "user_scores"} <= tables
        for forbidden in ("arena", "duels", "pvp", "rooms", "koth", "matches"):
            assert all(forbidden not in t.lower() for t in tables)


def test_no_default_admin_and_explicit_env_seeding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_no_admin = tmp_path / "no_admin.db"
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("HASHED_PASSWORD", raising=False)
    db.init_db(db_no_admin)
    assert db.get_admin_stats(db_no_admin)["users_total"] == 0
    assert db.get_admin_stats(db_no_admin)["admins_total"] == 0

    # Partial config (only username) must NOT create admin
    monkeypatch.setenv("ADMIN_USERNAME", "rootadmin")
    db.init_db(db_no_admin)
    assert db.get_admin_stats(db_no_admin)["users_total"] == 0

    # Plaintext password in HASHED_PASSWORD must fail fast
    monkeypatch.setenv("HASHED_PASSWORD", "plaintext-not-a-hash")
    with pytest.raises(RuntimeError):
        db.init_db(db_no_admin)

    # Explicit valid Argon2id hash seeds admin cleanly
    admin_hash = db.hash_password("SuperAdminPass123!")
    monkeypatch.setenv("HASHED_PASSWORD", admin_hash)
    db.init_db(db_no_admin)
    seeded = db.get_user_by_username("rootadmin", db_no_admin)
    assert seeded is not None
    assert seeded["role"] == "admin"


# ---------------------------------------------------------------------------
# 2. Argon2id hashing & transparent bcrypt auto-upgrade on login
# ---------------------------------------------------------------------------


def test_argon2id_hashing_and_bcrypt_auto_upgrade(isolated_env: dict[str, Any]) -> None:
    db_path: Path = isolated_env["db_path"]

    # New user gets Argon2id hash
    db.create_user("alice", "StrongPass123!", db_path=db_path)
    with db.get_connection(db_path) as conn:
        alice_hash = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'alice'"
        ).fetchone()["password_hash"]
    assert alice_hash.startswith("$argon2id$")

    # Legacy bcrypt user transparently authenticates and auto-upgrades to Argon2id
    legacy_pw = "LegacyBcryptPass9!"
    legacy_bcrypt = bcrypt.hashpw(
        legacy_pw.encode("utf-8"), bcrypt.gensalt(rounds=4)
    ).decode("utf-8")
    assert legacy_bcrypt.startswith("$2b$")

    with db.get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'user')",
            ("legacy_bob", legacy_bcrypt),
        )
        conn.commit()

    # Wrong password fails and does not upgrade
    assert db.authenticate_user("legacy_bob", "wrongpass", db_path=db_path) is None
    with db.get_connection(db_path) as conn:
        still_bcrypt = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'legacy_bob'"
        ).fetchone()["password_hash"]
    assert still_bcrypt == legacy_bcrypt

    # Valid login succeeds and upgrades hash in-place to Argon2id
    authed = db.authenticate_user("legacy_bob", legacy_pw, db_path=db_path)
    assert authed is not None
    assert authed["username"] == "legacy_bob"
    with db.get_connection(db_path) as conn:
        upgraded = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'legacy_bob'"
        ).fetchone()["password_hash"]
    assert upgraded.startswith("$argon2id$")
    assert db.authenticate_user("legacy_bob", legacy_pw, db_path=db_path) is not None


# ---------------------------------------------------------------------------
# 3. Session lifecycle: session_version revocation, IP/UA binding, Trial, cookies
# ---------------------------------------------------------------------------


def test_session_version_revocation_on_logout(client: TestClient) -> None:
    reg = client.post(
        "/register",
        data={"username": "student1", "password": "Password123!"},
        follow_redirects=False,
    )
    assert reg.status_code == 303
    old_session = client.cookies.get("uni_session")
    assert old_session

    # Logged in
    portal_ok = client.get("/api/portal").json()
    assert portal_ok["user"]["username"] == "student1"
    assert portal_ok["user"]["is_authenticated"] is True

    # Logout increments session_version in DB
    out = client.post("/logout", follow_redirects=False)
    assert out.status_code == 303
    user_row = db.get_user_by_username("student1")
    assert user_row is not None
    assert user_row["session_version"] == 2

    # Replaying old session cookie MUST fail (revoked by session_version)
    client.cookies.set("uni_session", old_session)
    portal_after = client.get("/api/portal").json()
    assert portal_after["user"] is None


def test_session_ip_and_ua_binding(isolated_env: dict[str, Any]) -> None:
    app = create_app()
    with TestClient(app) as tc:
        ua_headers = {
            "User-Agent": "Mozilla/5.0 UnitTestA",
            "X-Forwarded-For": "203.0.113.10",
        }
        res = tc.post(
            "/register",
            data={"username": "bound_user", "password": "Password123!"},
            headers=ua_headers,
            follow_redirects=False,
        )
        assert res.status_code == 303

        # Same IP + UA works
        assert (
            tc.get("/api/portal", headers=ua_headers).json()["user"]["username"]
            == "bound_user"
        )

        # Different User-Agent rejected
        diff_ua = {
            "User-Agent": "Mozilla/5.0 Hijacker",
            "X-Forwarded-For": "203.0.113.10",
        }
        assert tc.get("/api/portal", headers=diff_ua).json()["user"] is None

        # Different IP rejected
        diff_ip = {
            "User-Agent": "Mozilla/5.0 UnitTestA",
            "X-Forwarded-For": "198.51.100.77",
        }
        assert tc.get("/api/portal", headers=diff_ip).json()["user"] is None


def test_trial_lightweight_cookie_without_db_row(
    client: TestClient,
    isolated_env: dict[str, Any],
) -> None:
    before_count = db.get_admin_stats(isolated_env["db_path"])["users_total"]
    resp = client.post("/trial", follow_redirects=False)
    assert resp.status_code == 303
    assert client.cookies.get("uni_trial")

    after_count = db.get_admin_stats(isolated_env["db_path"])["users_total"]
    assert after_count == before_count

    data = client.get("/api/portal").json()
    assert data["user"] is not None
    assert data["user"]["role"] == "trial"
    assert data["user"]["is_authenticated"] is False


def test_cookie_security_flags(
    isolated_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app()
    with TestClient(app) as tc:
        resp_https = tc.post(
            "/register",
            data={"username": "secure_u", "password": "Password123!"},
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        set_cookies = resp_https.headers.get_list("set-cookie")
        session_hdr = next(c for c in set_cookies if c.startswith("uni_session="))
        lowered = session_hdr.lower()
        assert "httponly" in lowered
        assert "samesite=lax" in lowered
        assert "secure" in lowered
        assert "strict-transport-security" in resp_https.headers

    # Also verify COOKIE_SECURE=1 forces Secure even on plain HTTP
    monkeypatch.setenv("COOKIE_SECURE", "1")
    with TestClient(app) as tc:
        resp_forced = tc.post(
            "/trial",
            follow_redirects=False,
        )
        trial_hdr = next(
            c
            for c in resp_forced.headers.get_list("set-cookie")
            if c.startswith("uni_trial=")
        )
        assert "secure" in trial_hdr.lower()
        assert "httponly" in trial_hdr.lower()


def test_next_redirect_validation() -> None:
    assert validate_next_url("/predmet/num") == "/predmet/num"
    assert validate_next_url("/predmet/num?mode=exam") == "/predmet/num?mode=exam"
    for bad in (
        None,
        "",
        "https://evil.com/phish",
        "http://evil.com",
        "//evil.com",
        "///evil.com",
        r"/\evil.com",
        "javascript:alert(1)",
        "/ok\r\nSet-Cookie: x=1",
        "predmet/num",
    ):
        assert validate_next_url(bad) == "/"


# ---------------------------------------------------------------------------
# 4. CSRF enforcement (NEVER bypassed by User-Agent: testclient!)
# ---------------------------------------------------------------------------


def test_csrf_enforced_including_testclient_ua_when_not_testing(
    isolated_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TESTING", "0")
    app = create_app()
    with TestClient(app) as tc:
        # Even with User-Agent: testclient, POST without CSRF MUST be 403
        ua_tc = {"User-Agent": "testclient"}
        assert (
            tc.post(
                "/register",
                data={"username": "csrf_u", "password": "Password123!"},
                headers=ua_tc,
            ).status_code
            == 403
        )
        assert tc.post("/trial", headers=ua_tc).status_code == 403
        assert tc.post("/logout", headers=ua_tc).status_code == 403
        assert (
            tc.put(
                "/api/preferences",
                json={"theme": "amoled"},
                headers=ua_tc,
            ).status_code
            == 403
        )

        # Acquire legitimate CSRF cookie via GET /register
        token = _get_csrf(tc, "/register")

        # text/plain CSRF attack rejected even when cookie is present
        assert (
            tc.post(
                "/logout",
                content=f"csrf_token={token}",
                headers={"Content-Type": "text/plain", "User-Agent": "testclient"},
            ).status_code
            == 403
        )

        # Wrong token rejected
        assert (
            tc.post(
                "/register",
                data={
                    "username": "csrf_u",
                    "password": "Password123!",
                    "csrf_token": "x" * 40,
                },
                headers=ua_tc,
            ).status_code
            == 403
        )

        # Valid form csrf_token succeeds
        reg_ok = tc.post(
            "/register",
            data={
                "username": "csrf_u",
                "password": "Password123!",
                "csrf_token": token,
            },
            headers=ua_tc,
            follow_redirects=False,
        )
        assert reg_ok.status_code == 303

        # PUT /api/preferences requires X-CSRF-Token header
        assert (
            tc.put(
                "/api/preferences", json={"theme": "amoled"}, headers=ua_tc
            ).status_code
            == 403
        )
        put_ok = tc.put(
            "/api/preferences",
            json={"theme": "amoled"},
            headers={**ua_tc, "X-CSRF-Token": token},
        )
        assert put_ok.status_code == 200
        assert put_ok.json()["preferences"]["theme"] == "amoled"

        # POST /logout requires CSRF
        assert tc.post("/logout", headers=ua_tc).status_code == 403
        out_ok = tc.post(
            "/logout",
            headers={**ua_tc, "X-CSRF-Token": token},
            follow_redirects=False,
        )
        assert out_ok.status_code == 303


# ---------------------------------------------------------------------------
# 5. Preferences strict allowlist & HTML data-* attributes
# ---------------------------------------------------------------------------


def test_preferences_allowlist_and_api(client: TestClient) -> None:
    # Unauthenticated PUT -> 401
    assert client.put("/api/preferences", json={"theme": "amoled"}).status_code == 401

    # Register & update with valid values + unknown malicious keys (dropped)
    client.post(
        "/register",
        data={"username": "pref_user", "password": "Password123!"},
        follow_redirects=False,
    )
    resp = client.put(
        "/api/preferences",
        json={
            "theme": "catppuccin",
            "accent": "emerald",
            "density": "compact",
            "font_scale": 110,
            "focus_mode": True,
            "show_matrix": False,
            "rail_state": "collapsed",
            "shortcuts": False,
            "auto_advance": True,
            "sound": True,
            "code_layout": "stack",
            "reduced_motion": True,
            "formula_hints": False,
            "daily_goal": 20,
            "block_size": 15,
            "pinned_subjects": ["num", "pas", "num"],
            "custom_css": "body { background: url(https://evil.example/x); }",
            "style": "color:red",
            "role": "admin",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    prefs = body["preferences"]
    assert "custom_css" not in prefs
    assert "style" not in prefs
    assert "role" not in prefs
    assert prefs["theme"] == "catppuccin"
    assert prefs["pinned_subjects"] == ["num", "pas"]

    attrs = body["html_attrs"]
    expected_attr_keys = {
        "data-theme",
        "data-accent",
        "data-density",
        "data-font-scale",
        "data-focus",
        "data-matrix",
        "data-rail",
        "data-shortcuts",
        "data-auto-advance",
        "data-sound",
        "data-code-layout",
        "data-reduced-motion",
    }
    assert set(attrs.keys()) == expected_attr_keys
    assert attrs["data-theme"] == "catppuccin"
    assert attrs["data-focus"] == "1"
    assert attrs["data-matrix"] == "0"
    assert attrs["data-font-scale"] == "110"

    # Invalid values strictly rejected (422)
    invalid_payloads = [
        {"theme": "graphite; } body { color: red"},
        {"accent": "#ff0000"},
        {"font_scale": 150},
        {"font_scale": True},
        {"daily_goal": 2},
        {"daily_goal": 31},
        {"daily_goal": True},
        {"block_size": 7},
        {"focus_mode": "yes"},
        {"pinned_subjects": ["../etc/passwd"]},
        {"pinned_subjects": [f"s{i}" for i in range(21)]},
    ]
    for bad in invalid_payloads:
        assert client.put("/api/preferences", json=bad).status_code == 422
        with pytest.raises(ValueError):
            validate_preferences_payload(bad)

    # Sanitizer falls back safely on corrupted input
    sanitized = sanitize_preferences(
        {"theme": "evil", "custom_css": "x", "daily_goal": 999}
    )
    assert sanitized == DEFAULT_PREFERENCES
    assert prefs_to_html_attrs(sanitized)["data-theme"] == "graphite"


# ---------------------------------------------------------------------------
# 6. Zero public leakage & 404 anti-enumeration + isolated admin
# ---------------------------------------------------------------------------


def test_zero_public_leakage_and_404_anti_enumeration(
    client: TestClient,
    isolated_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Anonymous checks
    home = client.get("/")
    assert home.status_code == 200
    for leaked in (
        "/admin",
        "draft_secret",
        "Tajný",
        "obsidian",
        "PRIVATE_OBSIDIAN_NUM",
    ):
        assert leaked.lower() not in home.text.lower()

    portal_json = client.get("/api/portal").text.lower()
    for leaked in (
        "/admin",
        "draft_secret",
        "obsidian",
        "provider",
        "secret_vault_folder",
    ):
        assert leaked not in portal_json

    for q in ("", "draft", "tajný", "kryptotajný", "obsidian", "admin"):
        search_payload = client.get("/api/search", params={"q": q}).json()
        results_text = str(search_payload["results"]).lower()
        for leaked in ("draft_secret", "tajný", "kryptotajný", "obsidian", "/admin"):
            assert leaked not in results_text
        if q:
            assert search_payload["results"] == []

    # Public search still finds public subject, topic, glossary, formula
    search_bis = client.get("/api/search", params={"q": "bisekce"}).json()
    assert any(
        r["type"] == "glossary" and r["title"] == "Bisekce"
        for r in search_bis["results"]
    )
    search_newt = client.get("/api/search", params={"q": "newtonova"}).json()
    assert any(r["type"] == "formula" for r in search_newt["results"])

    # Anti-enumeration 404 for anonymous user
    hidden_paths = [
        "/admin",
        "/admin/assets/admin.js",
        "/admin/assets/admin.css",
        "/api/admin/overview",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/predmet/draft_secret",
        "/predmet/draft_secret/code",
    ]
    for path in hidden_paths:
        assert client.get(path).status_code == 404, f"Expected 404 on {path}"

    # POST to admin API without CSRF by non-admin MUST return 404 (not 403!)
    monkeypatch.setenv("TESTING", "0")
    assert (
        client.post(
            "/api/admin/subjects/num/visibility",
            json={"visibility": "admin"},
        ).status_code
        == 404
    )
    monkeypatch.setenv("TESTING", "1")

    # Regular authenticated student ALSO gets 404 on all admin/draft endpoints
    client.post(
        "/register",
        data={"username": "regular_student", "password": "Password123!"},
        follow_redirects=False,
    )
    for path in hidden_paths:
        assert client.get(path).status_code == 404
    assert (
        client.post(
            "/api/admin/subjects/num/visibility",
            json={"visibility": "admin"},
        ).status_code
        == 404
    )


def test_admin_access_assets_and_visibility_toggle(
    isolated_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path: Path = isolated_env["db_path"]
    db.create_user("site_admin", "AdminSecret123!", role="admin", db_path=db_path)

    app = create_app()
    with TestClient(app) as tc:
        login = tc.post(
            "/login",
            data={"username": "site_admin", "password": "AdminSecret123!"},
            follow_redirects=False,
        )
        assert login.status_code == 303

        # Admin panel and assets work
        adm = tc.get("/admin")
        assert adm.status_code == 200
        assert "draft_secret" in adm.text

        js = tc.get("/admin/assets/admin.js")
        assert js.status_code == 200
        assert "javascript" in js.headers["content-type"]

        css = tc.get("/admin/assets/admin.css")
        assert css.status_code == 200
        assert "text/css" in css.headers["content-type"]

        # Strict allowlist blocks arbitrary files or traversal
        assert tc.get("/admin/assets/admin.html").status_code == 404
        assert tc.get("/admin/assets/..%2Fadmin.html").status_code == 404

        # Admin overview includes provider and draft
        ov = tc.get("/api/admin/overview")
        assert ov.status_code == 200
        subjects_by_slug = {s["slug"]: s for s in ov.json()["subjects"]}
        assert "draft_secret" in subjects_by_slug
        assert subjects_by_slug["draft_secret"]["visibility"] == "admin"
        assert (
            subjects_by_slug["draft_secret"]["provider"]["folder"]
            == "SECRET_VAULT_FOLDER"
        )

        # Admin can visit hidden draft subject directly
        assert tc.get("/predmet/draft_secret").status_code == 200

        # Toggle visibility requires CSRF when TESTING=0
        monkeypatch.setenv("TESTING", "0")
        assert (
            tc.post(
                "/api/admin/subjects/draft_secret/visibility",
                json={"visibility": "public"},
            ).status_code
            == 403
        )
        csrf_tok = _get_csrf(tc, "/admin")
        toggled = tc.post(
            "/api/admin/subjects/draft_secret/visibility",
            json={"visibility": "public"},
            headers={"X-CSRF-Token": csrf_tok},
        )
        assert toggled.status_code == 200
        assert toggled.json()["visibility"] == "public"

    # Now anonymous user can see draft_secret
    with TestClient(app) as anon:
        assert anon.get("/predmet/draft_secret").status_code == 200


# ---------------------------------------------------------------------------
# 7. Security headers, CSP isolation on /code, mastery levels, external redirect
# ---------------------------------------------------------------------------


def test_security_headers_and_code_route_csp(client: TestClient) -> None:
    home = client.get("/")
    csp = home.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self';" in csp
    assert "wasm-unsafe-eval" not in csp
    assert "require-trusted-types-for 'script'" in csp
    assert "trusted-types 'none'" in csp
    assert home.headers["x-content-type-options"] == "nosniff"
    assert home.headers["x-frame-options"] == "DENY"
    assert home.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert home.headers["cross-origin-opener-policy"] == "same-origin"
    assert home.headers["cross-origin-resource-policy"] == "same-origin"
    assert "strict-transport-security" not in home.headers

    code_page = client.get("/predmet/num/code")
    assert code_page.status_code == 200
    code_csp = code_page.headers["content-security-policy"]
    assert "'wasm-unsafe-eval'" in code_csp
    assert "worker-src 'self' blob:" in code_csp
    assert "require-trusted-types-for 'script'" in code_csp

    https_health = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert https_health.status_code == 200
    assert https_health.json() == {"status": "ok"}
    assert "max-age=31536000" in https_health.headers["strict-transport-security"]


def test_external_subject_redirect_and_mastery_levels(client: TestClient) -> None:
    ext = client.get("/predmet/tzi", follow_redirects=False)
    assert ext.status_code == 302
    assert ext.headers["location"] == "https://tzi.rutterle.eu"
    assert client.get("/predmet/tzi/code", follow_redirects=False).status_code == 404

    qids = ["q1", "q2", "q3", "q4"]
    m0 = compute_mastery(qids, {})
    assert m0["mastery_level"] == "unstarted"
    assert m0["mastery_label"] == "Neprobráno"
    assert m0["cells"] == ["·", "·", "·", "·"]

    m1 = compute_mastery(
        qids,
        {"q1": {"correct_count": 1, "wrong_count": 0, "last_correct": 1, "lapses": 0}},
    )
    assert m1["mastery_level"] == "familiar"
    assert m1["mastery_label"] == "Seznámeno"
    assert m1["cells"] == ["1", "·", "·", "·"]

    m2 = compute_mastery(
        qids,
        {
            "q1": {
                "correct_count": 1,
                "wrong_count": 0,
                "last_correct": 1,
                "lapses": 0,
            },
            "q2": {
                "correct_count": 1,
                "wrong_count": 0,
                "last_correct": 1,
                "lapses": 0,
            },
            "q3": {
                "correct_count": 0,
                "wrong_count": 1,
                "last_correct": 0,
                "lapses": 1,
            },
        },
    )
    assert m2["mastery_level"] == "proficient"
    assert m2["mastery_label"] == "Jisté"
    assert m2["cells"] == ["1", "1", "0", "·"]

    m3 = compute_mastery(
        qids,
        {
            qid: {
                "correct_count": 3,
                "wrong_count": 0,
                "last_correct": 1,
                "stability": 5.0,
                "lapses": 0,
            }
            for qid in qids
        },
    )
    assert m3["mastery_level"] == "mastered"
    assert m3["mastery_label"] == "Zvládnuto"
    assert m3["progress_pct"] == 100
    assert m3["readiness_pct"] == 100
