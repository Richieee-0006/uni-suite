"""Security hardening regression tests (rate limits, DoS, headers, sandbox)."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from fastapi.testclient import TestClient

import db
from webapp.routers import auth as auth_router
from webapp.routers import feedback as feedback_router
from webapp.routers import sandbox as sandbox_router
from webapp.routers import study as study_router
from webapp.sandbox.base import (
    ExecutionRequest,
    communicate_bounded,
)
from webapp.sandbox.drivers.r_docker import RESULT_SENTINEL, RDockerDriver
from webapp.tools.automata import MAX_ALPHABET


def test_login_rate_limit_blocks_ua_rotation(client: TestClient) -> None:
    """POST /login enforces IP-based quota even when User-Agent rotates."""
    auth_router.login_rate_limiter.reset()
    for i in range(10):
        resp = client.post(
            "/login",
            data={"username": "nobody", "password": "wrongpassword"},
            headers={
                "CF-Connecting-IP": "198.51.100.10",
                "User-Agent": f"BruteBot/{i}",
            },
        )
        assert resp.status_code == 400

    blocked = client.post(
        "/login",
        data={"username": "nobody", "password": "wrongpassword"},
        headers={
            "CF-Connecting-IP": "198.51.100.10",
            "User-Agent": "FreshUserAgent/999",
        },
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1

    # Different client IP keeps its own quota.
    other_ip = client.post(
        "/login",
        data={"username": "nobody", "password": "wrongpassword"},
        headers={"CF-Connecting-IP": "198.51.100.11"},
    )
    assert other_ip.status_code == 400


def test_register_and_trial_rate_limits(client: TestClient) -> None:
    """POST /register and POST /trial enforce IP-based rate limits."""
    auth_router.register_rate_limiter.reset()
    for i in range(5):
        resp = client.post(
            "/register",
            data={"username": "ab", "password": "short"},
            headers={"CF-Connecting-IP": "198.51.100.20", "User-Agent": f"Bot/{i}"},
        )
        assert resp.status_code == 400

    blocked_reg = client.post(
        "/register",
        data={"username": "validuser", "password": "ValidPassword123"},
        headers={"CF-Connecting-IP": "198.51.100.20", "User-Agent": "Other/1"},
    )
    assert blocked_reg.status_code == 429
    assert int(blocked_reg.headers["retry-after"]) >= 1

    auth_router.trial_rate_limiter.reset()
    for i in range(15):
        resp = client.post(
            "/trial",
            headers={
                "Accept": "application/json",
                "CF-Connecting-IP": "198.51.100.21",
                "User-Agent": f"TrialBot/{i}",
            },
        )
        assert resp.status_code == 200

    blocked_trial = client.post(
        "/trial",
        headers={
            "Accept": "application/json",
            "CF-Connecting-IP": "198.51.100.21",
            "User-Agent": "TrialBot/rotated",
        },
    )
    assert blocked_trial.status_code == 429
    assert int(blocked_trial.headers["retry-after"]) >= 1


def test_feedback_guest_rate_limit_ignores_ua_rotation(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    """Guest feedback rate limit keys strictly by IP, not User-Agent."""
    feedback_router.feedback_rate_limiter.reset()
    monkeypatch.setenv("GITHUB_ISSUES_TOKEN", "ghp_testtoken1234567890")

    async def _fake_issue(**_kwargs: Any) -> tuple[int, str]:
        return 7, "https://github.com/Richieee-0006/uni-suite/issues/7"

    monkeypatch.setattr(feedback_router, "_create_github_issue", _fake_issue)

    payload = {
        "kind": "bug",
        "title": "Test feedback item",
        "description": "Long enough valid description for feedback.",
    }
    for i in range(5):
        resp = client.post(
            "/api/feedback",
            json=payload,
            headers={
                "CF-Connecting-IP": "198.51.100.30",
                "User-Agent": f"RotatingUA/{i}",
            },
        )
        assert resp.status_code == 201

    blocked = client.post(
        "/api/feedback",
        json=payload,
        headers={
            "CF-Connecting-IP": "198.51.100.30",
            "User-Agent": "CompletelyDifferentUA/99",
        },
    )
    assert blocked.status_code == 429


def test_sandbox_trial_rate_limit_key_ignores_ua(client: TestClient) -> None:
    """Trial sandbox rate-limit key does not vary with User-Agent."""
    sandbox_router.sandbox_rate_limiter.reset()
    req_a = type(
        "_Req",
        (),
        {
            "headers": {
                "cf-connecting-ip": "198.51.100.40",
                "user-agent": "AgentA",
            },
            "client": None,
        },
    )()
    req_b = type(
        "_Req",
        (),
        {
            "headers": {
                "cf-connecting-ip": "198.51.100.40",
                "user-agent": "AgentB",
            },
            "client": None,
        },
    )()
    trial_user = {"id": None, "is_authenticated": False, "is_trial": True}
    assert sandbox_router._rate_limit_key(req_a, trial_user) == (
        sandbox_router._rate_limit_key(req_b, trial_user)
    )


def test_automata_caps_alphabet_body_and_rate(client: TestClient) -> None:
    """Automata endpoint rejects oversized alphabet, oversized body, and bursts."""
    study_router.automata_rate_limiter.reset()

    big_alphabet = [f"s{i}" for i in range(MAX_ALPHABET + 1)]
    resp = client.post(
        "/api/num/tools/automata",
        json={
            "action": "simulate",
            "automaton": {
                "states": ["q0"],
                "alphabet": big_alphabet,
                "start": "q0",
                "accept": ["q0"],
                "transitions": {},
            },
            "word": "",
        },
        headers={"CF-Connecting-IP": "198.51.100.50"},
    )
    assert resp.status_code == 400

    oversized = client.post(
        "/api/num/tools/automata",
        content=("x" * (study_router.MAX_AUTOMATA_BODY_BYTES + 128)).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "CF-Connecting-IP": "198.51.100.50",
        },
    )
    assert oversized.status_code == 413

    study_router.automata_rate_limiter.reset()
    small_body = {
        "action": "simulate",
        "automaton": {
            "states": ["q0"],
            "alphabet": ["0"],
            "start": "q0",
            "accept": ["q0"],
            "transitions": {"q0": {"0": "q0"}},
        },
        "word": "0",
    }
    for _ in range(30):
        ok = client.post(
            "/api/num/tools/automata",
            json=small_body,
            headers={"CF-Connecting-IP": "198.51.100.51"},
        )
        assert ok.status_code == 200

    limited = client.post(
        "/api/num/tools/automata",
        json=small_body,
        headers={"CF-Connecting-IP": "198.51.100.51"},
    )
    assert limited.status_code == 429


def test_cache_control_no_store_on_html_and_authed_api(client: TestClient) -> None:
    """HTML pages and authenticated API responses carry private no-store."""
    home = client.get("/")
    assert home.status_code == 200
    assert "no-store" in home.headers.get("cache-control", "")

    reg = client.post(
        "/register",
        data={
            "username": "cachetester",
            "password": "StrongPassword123!",
            "password_confirm": "StrongPassword123!",
        },
        follow_redirects=False,
    )
    assert reg.status_code == 303

    portal_api = client.get("/api/portal")
    assert portal_api.status_code == 200
    assert "no-store" in portal_api.headers.get("cache-control", "")


def test_communicate_bounded_caps_runaway_stdout() -> None:
    """communicate_bounded stops runaway subprocess output at the byte cap."""

    async def _run() -> tuple[bytes, bytes, bool]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('A' * 500000); sys.stdout.flush()",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return await communicate_bounded(
            proc,
            b"",
            timeout_sec=5.0,
            max_stdout_bytes=4096,
            max_stderr_bytes=4096,
        )

    out_b, _, exceeded = asyncio.run(_run())
    assert exceeded is True
    assert len(out_b) <= 4096


def test_r_docker_per_run_sentinel_rejects_static_spoof() -> None:
    """Using a per-run sentinel ignores a student-forged static RESULT_SENTINEL."""
    driver = RDockerDriver()
    req = ExecutionRequest(language="r", code="1+1")
    forged = f'noise\n{RESULT_SENTINEL}\n{{"ok": true, "stdout": "spoofed"}}\n'
    res = driver._parse_harness_output(
        forged,
        "",
        req,
        elapsed_ms=10,
        sentinel="__UNI_SBX_R_unique_random_token__",
    )
    assert res.ok is False
    assert res.status == "error"


def test_authenticate_user_missing_account_returns_none(
    isolated_env: dict[str, Any],
) -> None:
    """Missing username runs dummy verification path and returns None cleanly."""
    assert db.authenticate_user("does_not_exist_xyz", "SomePass123!") is None
