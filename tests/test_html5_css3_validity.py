"""Automated HTML5 Living Standard & zero-leakage SSR validation gate."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import db
from webapp import config as cfg
from webapp import subjects as subjects_mod
from webapp.main import create_app

BASE_DIR = Path(__file__).resolve().parent.parent
HTMLVALIDATE_CFG = BASE_DIR / ".htmlvalidate.json"


def _npx_bin() -> str | None:
    return shutil.which("npx")


def _run_html_validate(files: list[str]) -> None:
    npx = _npx_bin()
    if not npx:
        pytest.skip("npx not installed in environment")
    proc = subprocess.run(  # noqa: S603
        [npx, "-y", "html-validate", "--config", str(HTMLVALIDATE_CFG), *files],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
    )
    assert proc.returncode == 0, f"html-validate errors:\n{proc.stdout}\n{proc.stderr}"


def test_rendered_ssr_pages_valid_html5_and_zero_leakage(
    client: TestClient, isolated_env: dict[str, Any], tmp_path: Path
) -> None:
    public_routes = {
        "portal.html": "/",
        "login.html": "/login",
        "register.html": "/register",
        "subject_num.html": "/predmet/num",
        "subject_num_code.html": "/predmet/num/code",
    }

    written: list[str] = []
    for fname, route in public_routes.items():
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} returned {resp.status_code}"
        html = resp.text
        lowered = html.lower()
        assert "<!doctype html>" in lowered
        assert "/admin" not in lowered, f"Leaked /admin on {route}"
        assert "obsidian" not in lowered, f"Leaked obsidian on {route}"
        assert "draft_secret" not in lowered, f"Leaked draft subject on {route}"
        assert str(Path.home()).lower() not in lowered, f"Leaked personal path on {route}"
        assert "c:\\users\\" not in lowered, f"Leaked Windows path on {route}"
        assert "onclick=" not in lowered, f"Inline onclick on {route}"
        assert 'style="' not in lowered, f"Inline style= on {route}"
        out = tmp_path / fname
        out.write_text(html, encoding="utf-8")
        written.append(str(out))

    # Create temporary admin account in isolated DB and validate SSR /admin HTML5
    admin_pw = "AdminValidPass#2026"
    db.create_user(
        "html5_admin",
        admin_pw,
        role="admin",
        db_path=isolated_env["db_path"],
    )
    r_login = client.post(
        "/login",
        data={"username": "html5_admin", "password": admin_pw},
        follow_redirects=False,
    )
    assert r_login.status_code == 303
    resp_admin = client.get("/admin")
    assert resp_admin.status_code == 200
    admin_out = tmp_path / "admin.html"
    admin_out.write_text(resp_admin.text, encoding="utf-8")
    written.append(str(admin_out))

    _run_html_validate(written)


def test_real_catalog_subjects_valid_html5(
    isolated_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_subjects = BASE_DIR / "subjects"
    monkeypatch.setattr(cfg, "SUBJECTS_DIR", real_subjects)
    monkeypatch.setattr(subjects_mod, "SUBJECTS_DIR", real_subjects)

    app = create_app()
    routes = {
        "real_portal.html": "/",
        "real_num.html": "/predmet/num",
        "real_num_code.html": "/predmet/num/code",
        "real_pas.html": "/predmet/pas",
        "real_pas_code.html": "/predmet/pas/code",
        "real_tzi.html": "/predmet/tzi",
    }
    written: list[str] = []
    with TestClient(app) as tc:
        for fname, route in routes.items():
            resp = tc.get(route)
            assert resp.status_code == 200, f"{route} returned {resp.status_code}"
            assert "obsidian" not in resp.text.lower()
            out = tmp_path / fname
            out.write_text(resp.text, encoding="utf-8")
            written.append(str(out))

        # Verify official KI/NUM syllabus & dual R/Python sandbox non-leakage
        num_html = (tmp_path / "real_num.html").read_text(encoding="utf-8")
        for token in (
            "Praktický test",
            "Rombergova kvadratura",
            "Vicher, M.",
            "VONDRÁK, V.",
            "KUBEN, J.",
        ):
            assert token in num_html, f"Missing NUM syllabus content: {token}"

        r_code_q = tc.get("/api/num/questions?mode=practice&type=code_task&limit=50")
        assert r_code_q.status_code == 200
        q_list = r_code_q.json().get("questions", [])
        assert len(q_list) == 10
        for item in q_list:
            assert "solution_code" not in item
            assert "hidden_tests" not in item
            langs = item.get("languages") or {}
            assert {"r", "python"} <= set(langs.keys())
            for variant in langs.values():
                assert "solution_code" not in variant
                assert "hidden_tests" not in variant
                assert variant.get("starter_code")
                assert len(variant.get("visible_tests") or []) >= 2

    _run_html_validate(written)
