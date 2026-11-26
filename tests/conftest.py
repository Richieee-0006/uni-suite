"""Pytest configuration and isolated fixtures for UNI Suite backend & security tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Allow importing webapp.main without a production .env APP_SECRET.
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("COOKIE_SECURE", "0")

import db  # noqa: E402
from webapp import config as cfg  # noqa: E402
from webapp import subjects as subjects_mod  # noqa: E402
from webapp.main import create_app  # noqa: E402
from webapp.routers import admin as admin_router  # noqa: E402
from webapp.routers import portal as portal_router  # noqa: E402

_FALLBACK_PUBLIC_TEMPLATES: dict[str, str] = {
    "portal.html": (
        '<!DOCTYPE html><html lang="cs" '
        '{% for k, v in html_attrs.items() %}{{ k }}="{{ v }}" {% endfor %}>'
        "<head><title>{{ page_title }}</title>"
        '<meta name="csrf-token" content="{{ csrf_token }}"></head>'
        "<body><h1>{{ site.title }}</h1>"
        "{% if user and user.role == 'admin' %}<a href=\"/admin\">Správa</a>{% endif %}"
        "<ul>{% for s in subjects %}<li>{{ s.code }} {{ s.title }} "
        "{{ s.mastery_label }}</li>{% endfor %}</ul>"
        "<div>{{ daily.done }}/{{ daily.goal }}</div></body></html>"
    ),
    "subject.html": (
        '<!DOCTYPE html><html lang="cs" '
        '{% for k, v in html_attrs.items() %}{{ k }}="{{ v }}" {% endfor %}>'
        "<head><title>{{ page_title }}</title>"
        '<meta name="csrf-token" content="{{ csrf_token }}"></head>'
        "<body><h1>{{ subject.code }} {{ subject.title }}</h1>"
        "<p>code={{ is_code_route }} err={{ error_count }} due={{ due_count }}</p>"
        "<ul>{% for t in topics %}<li>{{ t.title }} {{ t.mastery_label }}</li>"
        "{% endfor %}</ul></body></html>"
    ),
    "auth.html": (
        '<!DOCTYPE html><html lang="cs" '
        '{% for k, v in html_attrs.items() %}{{ k }}="{{ v }}" {% endfor %}>'
        "<head><title>{{ page_title }}</title>"
        '<meta name="csrf-token" content="{{ csrf_token }}"></head>'
        "<body><h1>{{ mode }}</h1>"
        '{% if error %}<p role="alert">{{ error }}</p>{% endif %}'
        '<form method="post"><input type="hidden" name="csrf_token" '
        'value="{{ csrf_token }}">'
        '<input type="hidden" name="next" value="{{ next_url }}"></form>'
        "</body></html>"
    ),
}

_FALLBACK_ADMIN_FILES: dict[str, str] = {
    "admin.html": (
        '<!DOCTYPE html><html lang="cs" '
        '{% for k, v in html_attrs.items() %}{{ k }}="{{ v }}" {% endfor %}>'
        "<head><title>{{ page_title }}</title>"
        '<link rel="stylesheet" href="{{ admin_assets_base }}/admin.css">'
        '<script src="{{ admin_assets_base }}/admin.js" defer></script></head>'
        "<body><h1>Admin</h1><p>users={{ stats.users_total }}</p>"
        "<ul>{% for s in all_subjects %}<li>{{ s.slug }}:{{ s.visibility }}</li>"
        "{% endfor %}</ul></body></html>"
    ),
    "admin.css": "body { margin: 0; }\n",
    "admin.js": "document.documentElement.dataset.adminReady = '1';\n",
}


def _populate_test_subjects(subjects_root: Path) -> None:
    """Create a realistic isolated subjects tree including a hidden admin draft."""
    subjects_root.mkdir(parents=True, exist_ok=True)
    registry = {
        "site": {
            "title": "UNI Suite",
            "domain": "uni.rutterle.eu",
            "subtitle": "Test catalog",
        },
        "subjects": [
            {"slug": "num", "kind": "local"},
            {"slug": "draft_secret", "kind": "local"},
            {
                "slug": "tzi",
                "kind": "external",
                "code": "KI/TIN",
                "short": "TZI",
                "title": "Teoretická informatika",
                "description": "Externí předmět",
                "url": "https://tzi.rutterle.eu",
                "status": "active",
                "visibility": "public",
            },
        ],
    }
    (subjects_root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    # Public local subject with bank, recall, glossary, and formulas
    num_dir = subjects_root / "num"
    (num_dir / "banky" / "doplnovacka").mkdir(parents=True, exist_ok=True)
    (num_dir / "glosar").mkdir(parents=True, exist_ok=True)
    (num_dir / "meta.json").write_text(
        json.dumps(
            {
                "slug": "num",
                "code": "KI/NUM",
                "short": "NUM",
                "title": "Numerické metody",
                "description": "Chyby výpočtu a hledání kořenů",
                "visibility": "public",
                "provider": {"type": "obsidian", "folder": "PRIVATE_OBSIDIAN_NUM"},
                "engines": ["choice", "input_numeric", "code_task"],
                "sandbox": {
                    "languages": ["r"],
                    "default_tier": "webr",
                    "timeout_ms": 5000,
                },
                "tools": ["glossary", "formulas"],
                "gamification": {"xp": True, "daily_goal": True},
                "exam_config": {"count": 10, "minutes": 20, "pass_pct": 60},
            }
        ),
        encoding="utf-8",
    )
    (num_dir / "banky" / "01-koreny.json").write_text(
        json.dumps(
            {
                "title": "Hledání kořenů",
                "lecture": 1,
                "date": "2026-03-01",
                "questions": [
                    {"id": "num-q1", "number": 1, "type": "choice", "text": "Bisekce"},
                    {"id": "num-q2", "number": 2, "type": "choice", "text": "Newton"},
                    {
                        "id": "num-q3",
                        "number": 3,
                        "type": "code_task",
                        "text": "R kořen",
                    },
                    {
                        "id": "num-q4",
                        "number": 4,
                        "type": "input_numeric",
                        "text": "Chyba",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (num_dir / "banky" / "doplnovacka" / "01-pojmy.json").write_text(
        json.dumps(
            {
                "title": "Základní pojmy",
                "lecture": 1,
                "questions": [
                    {
                        "id": "num-r1",
                        "number": 1,
                        "type": "recall",
                        "text": "Strojové epsilon",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (num_dir / "glosar" / "glosar.json").write_text(
        json.dumps(
            {
                "banks": {
                    "01-koreny": [
                        {
                            "term": "Bisekce",
                            "body": "Metoda půlení intervalu pro hledání kořene.",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (num_dir / "formulas.json").write_text(
        json.dumps(
            {
                "sections": [
                    {
                        "title": "Kořeny",
                        "items": [
                            {
                                "name": "Newtonova iterace",
                                "latex": "x_{n+1} = x_n - f(x_n)/f'(x_n)",
                                "note": "Kvadratická konvergence",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # Admin-only hidden draft subject
    draft_dir = subjects_root / "draft_secret"
    (draft_dir / "banky").mkdir(parents=True, exist_ok=True)
    (draft_dir / "glosar").mkdir(parents=True, exist_ok=True)
    (draft_dir / "meta.json").write_text(
        json.dumps(
            {
                "slug": "draft_secret",
                "code": "KI/SECRET",
                "short": "SEC",
                "title": "Tajný rozpracovaný předmět",
                "description": "Skrytý obsah jen pro admina",
                "visibility": "admin",
                "provider": {"type": "obsidian", "folder": "SECRET_VAULT_FOLDER"},
                "engines": ["choice"],
                "sandbox": {
                    "languages": ["r"],
                    "default_tier": "webr",
                    "timeout_ms": 5000,
                },
                "tools": ["glossary"],
            }
        ),
        encoding="utf-8",
    )
    (draft_dir / "banky" / "01-tajne.json").write_text(
        json.dumps(
            {
                "title": "Tajný okruh",
                "lecture": 1,
                "questions": [
                    {"id": "sec-q1", "number": 1, "type": "choice", "text": "Tajné"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (draft_dir / "glosar" / "glosar.json").write_text(
        json.dumps(
            {
                "banks": {
                    "01-tajne": [
                        {
                            "term": "Kryptotajný pojem",
                            "body": "Nesmí uniknout veřejnosti.",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Configure an isolated SQLite DB, subjects directory, and strong APP_SECRET."""
    db_file = tmp_path / "test_uni.db"
    subjects_root = tmp_path / "subjects"
    _populate_test_subjects(subjects_root)

    strong_secret = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    monkeypatch.setenv("APP_SECRET", strong_secret)
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("UNI_DB_PATH", str(db_file))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("HASHED_PASSWORD", raising=False)

    monkeypatch.setattr(cfg, "SUBJECTS_DIR", subjects_root)
    monkeypatch.setattr(subjects_mod, "SUBJECTS_DIR", subjects_root)

    # Use real ui/ templates & admin assets when present, or isolated fallbacks.
    pub_tpl_dir = cfg.TEMPLATES_DIR
    if not (pub_tpl_dir / "portal.html").is_file():
        pub_tpl_dir = tmp_path / "templates"
        pub_tpl_dir.mkdir(parents=True, exist_ok=True)
        for fname, content in _FALLBACK_PUBLIC_TEMPLATES.items():
            (pub_tpl_dir / fname).write_text(content, encoding="utf-8")

    admin_dir = cfg.ADMIN_UI_DIR
    if (
        not (admin_dir / "admin.html").is_file()
        or not (admin_dir / "admin.js").is_file()
    ):
        admin_dir = tmp_path / "admin_ui"
        admin_dir.mkdir(parents=True, exist_ok=True)
        for fname, content in _FALLBACK_ADMIN_FILES.items():
            (admin_dir / fname).write_text(content, encoding="utf-8")

    monkeypatch.setattr(cfg, "ADMIN_UI_DIR", admin_dir)
    monkeypatch.setattr(admin_router, "ADMIN_UI_DIR", admin_dir)
    monkeypatch.setattr(
        portal_router,
        "_public_templates",
        portal_router.build_templates(str(pub_tpl_dir)),
    )
    monkeypatch.setattr(
        admin_router,
        "_admin_templates",
        portal_router.build_templates(str(admin_dir)),
    )

    db.init_db(db_file)

    from webapp.routers import auth as auth_router
    from webapp.routers import feedback as feedback_router
    from webapp.routers import sandbox as sandbox_router
    from webapp.routers import study as study_router

    auth_router.login_rate_limiter.reset()
    auth_router.register_rate_limiter.reset()
    auth_router.trial_rate_limiter.reset()
    study_router.automata_rate_limiter.reset()
    feedback_router.feedback_rate_limiter.reset()
    sandbox_router.sandbox_rate_limiter.reset()

    return {
        "db_path": db_file,
        "subjects_dir": subjects_root,
        "admin_dir": admin_dir,
        "secret": strong_secret,
    }


@pytest.fixture()
def client(isolated_env: dict[str, Any]) -> TestClient:
    """Return a FastAPI TestClient wired to the isolated environment."""
    application = create_app()
    with TestClient(application) as tc:
        yield tc
