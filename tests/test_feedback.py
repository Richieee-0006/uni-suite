"""Tests for GitHub Issues feedback form endpoint."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from webapp.routers import feedback as feedback_router


@pytest.fixture(autouse=True)
def _reset_feedback_limiter() -> None:
    feedback_router.feedback_rate_limiter.reset()


def test_feedback_requires_server_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ISSUES_TOKEN", raising=False)
    resp = client.post(
        "/api/feedback",
        json={
            "kind": "bug",
            "title": "Rozbitý graf v NUM",
            "description": "Na mobilu se nevykreslí osa X v interaktivním grafu.",
            "subject": "num",
            "page_url": "/predmet/num",
        },
    )
    assert resp.status_code == 503
    assert "GITHUB_ISSUES_TOKEN" not in resp.text


def test_feedback_validation_errors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ISSUES_TOKEN", "ghp_test_token_value")
    # Invalid kind
    r1 = client.post(
        "/api/feedback",
        json={"kind": "spam", "title": "Test title", "description": "Valid description text."},
    )
    assert r1.status_code == 422

    # Short title
    r2 = client.post(
        "/api/feedback",
        json={"kind": "bug", "title": "abc", "description": "Valid description text."},
    )
    assert r2.status_code == 422

    # Short description
    r3 = client.post(
        "/api/feedback",
        json={"kind": "suggestion", "title": "Přidat tmavší režim", "description": "krátký"},
    )
    assert r3.status_code == 422

    # Invalid subject slug
    r4 = client.post(
        "/api/feedback",
        json={
            "kind": "bug",
            "title": "Špatný předmět",
            "description": "Popis chyby delší než deset znaků.",
            "subject": "../etc/passwd",
        },
    )
    assert r4.status_code == 422


def test_feedback_submits_github_issue_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ISSUES_TOKEN", "ghp_test_token_value")
    monkeypatch.setenv("GITHUB_ISSUES_REPO", "Richieee-0006/uni-suite")

    captured: dict[str, Any] = {}

    async def fake_create(
        *, token: str, repo: str, title: str, body: str, labels: list[str]
    ) -> tuple[int, str]:
        captured["token"] = token
        captured["repo"] = repo
        captured["title"] = title
        captured["body"] = body
        captured["labels"] = labels
        return 42, "https://github.com/Richieee-0006/uni-suite/issues/42"

    monkeypatch.setattr(feedback_router, "_create_github_issue", fake_create)

    resp = client.post(
        "/api/feedback",
        json={
            "kind": "suggestion",
            "subject": "num",
            "title": "Přidat příklad na Simpsonovo pravidlo",
            "description": "Hodilo by se víc řešených příkladů na numerickou integraci.",
            "contact": "@student",
            "page_url": "/predmet/num",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body == {
        "ok": True,
        "issue_number": 42,
        "issue_url": "https://github.com/Richieee-0006/uni-suite/issues/42",
    }
    assert captured["token"] == "ghp_test_token_value"
    assert captured["repo"] == "Richieee-0006/uni-suite"
    assert captured["title"] == "[NUM][Návrh] Přidat příklad na Simpsonovo pravidlo"
    assert captured["labels"] == ["enhancement"]
    assert "/predmet/num" in captured["body"]
    assert "@student" in captured["body"]
    assert resp.headers.get("cache-control") == "no-store"


def test_feedback_honeypot_absorbs_bots(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def fail_if_called(**_kwargs: Any) -> tuple[int, str]:
        nonlocal called
        called = True
        return 1, "https://github.com/Richieee-0006/uni-suite/issues/1"

    monkeypatch.setattr(feedback_router, "_create_github_issue", fail_if_called)

    resp = client.post(
        "/api/feedback",
        json={
            "kind": "bug",
            "title": "Buy cheap watches",
            "description": "Spam message body from a bot.",
            "website": "https://spam.example",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["issue_number"] is None
    assert called is False


def test_feedback_rate_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ISSUES_TOKEN", "ghp_test_token_value")

    async def fake_create(**_kwargs: Any) -> tuple[int, str]:
        return 7, "https://github.com/Richieee-0006/uni-suite/issues/7"

    monkeypatch.setattr(feedback_router, "_create_github_issue", fake_create)

    payload = {
        "kind": "bug",
        "title": "Opakované hlášení chyby",
        "description": "Podrobný popis chyby v testu rate limiteru.",
    }
    for _ in range(5):
        r = client.post("/api/feedback", json=payload)
        assert r.status_code == 201

    r_limited = client.post("/api/feedback", json=payload)
    assert r_limited.status_code == 429
    assert "retry-after" in r_limited.headers


def test_feedback_github_network_error_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ISSUES_TOKEN", "ghp_test_token_value")

    async def failing_post(self: Any, *_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(httpx.AsyncClient, "post", failing_post)

    resp = client.post(
        "/api/feedback",
        json={
            "kind": "bug",
            "title": "Chyba spojení",
            "description": "Testuje, že timeout GitHub API vrátí 502 bez úniku tokenu.",
        },
    )
    assert resp.status_code == 502
    assert "ghp_test_token_value" not in resp.text


def test_feedback_csrf_enforced_when_not_testing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESTING", "0")
    resp = client.post(
        "/api/feedback",
        json={
            "kind": "bug",
            "title": "Bez CSRF tokenu",
            "description": "Požadavek bez platného CSRF tokenu musí selhat.",
        },
    )
    assert resp.status_code == 403
