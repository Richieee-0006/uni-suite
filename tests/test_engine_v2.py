"""Comprehensive test suite for UNI Engine v2 (FSRS, generators, verifiers, tools)."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import db
from webapp import config as cfg
from webapp import subjects as subjects_mod
from webapp.engine import fsrs
from webapp.generators import (
    default_generator_registry,
    generate_question,
    list_generators_for_subject,
    reconstruct_generated_question,
)
from webapp.main import create_app
from webapp.routers import study as study_router
from webapp.tools import (
    are_equivalent,
    chi2_cdf,
    chi2_quantile,
    compute_bisection,
    compute_newton,
    normal_cdf,
    normal_pdf,
    normal_quantile,
    simulate_automaton,
    t_cdf,
    t_quantile,
)
from webapp.verifiers import (
    default_verifier_registry,
    levenshtein_distance,
    parse_numeric,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _populate_engine_subjects(subjects_root: Path) -> None:
    """Create isolated subjects tree with NUM, PAS, TZI-v1 bank, and hidden draft."""
    subjects_root.mkdir(parents=True, exist_ok=True)
    registry = {
        "site": {
            "title": "UNI Suite",
            "domain": "uni.rutterle.eu",
            "subtitle": "Engine v2 test catalog",
        },
        "subjects": [
            {"slug": "num", "kind": "local"},
            {"slug": "pas", "kind": "local"},
            {"slug": "draft_secret", "kind": "local"},
        ],
    }
    (subjects_root / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False), encoding="utf-8"
    )

    # NUM subject with choice, input_numeric, input, recall, multi_step, code_task
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
                "description": "Chyby, hledání kořenů, integrace",
                "visibility": "public",
                "engines": [
                    "choice",
                    "input_exact",
                    "input_numeric",
                    "recall",
                    "multi_step",
                    "code_task",
                ],
                "tools": ["glossary", "formulas", "numeric_lab"],
                "exam_config": {"count": 4, "minutes": 20, "pass_pct": 60},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (num_dir / "banky" / "01-zaklady.json").write_text(
        json.dumps(
            {
                "title": "01 · Základy NUM",
                "lecture": 1,
                "questions": [
                    {
                        "id": "num-q1",
                        "number": 1,
                        "type": "choice",
                        "text": (
                            "Jaký řád konvergence má Newtonova metoda "
                            "u jednoduchého kořene?"
                        ),
                        "options": {
                            "A": "Lineární",
                            "B": "Kvadratický",
                            "C": "Kubický",
                            "D": "Konstantní",
                        },
                        "correct": ["B"],
                        "hints": [
                            "Krok 1: Porovnej s bisekcí (lineární).",
                            "Krok 2: Platné cifry se v každém kroku zdvojnásobí.",
                            "Krok 3: Zdvojnásobení přesnosti odpovídá řádu 2.",
                        ],
                        "explanation": (
                            "Newtonova metoda má u jednoduchého kořene řád p = 2."
                        ),
                    },
                    {
                        "id": "num-q2",
                        "number": 2,
                        "type": "input_numeric",
                        "text": "Vypočti 17/12 na 4 desetinná místa.",
                        "answer": 17 / 12,
                        "tolerance": 1e-3,
                        "tolerance_mode": "abs",
                        "unit": "",
                        "hints": [
                            "Krok 1: Zlomek lze zadat i přímo jako 17/12.",
                            "Krok 2: 17 = 12 + 5, tedy 1 + 5/12.",
                            "Krok 3: 5/12 ≈ 0,4167.",
                        ],
                        "explanation": "17/12 = 1.416666...",
                    },
                    {
                        "id": "num-q3",
                        "number": 3,
                        "type": "input",
                        "text": "(37)₁₀ → (?)₂",
                        "hint": "výsledek ve dvojkové soustavě",
                        "answer": "100101",
                        "solution": "37 = 32 + 4 + 1 = 100101₂.",
                    },
                    {
                        "id": "num-q4",
                        "number": 4,
                        "type": "multi_step",
                        "text": "Dvoukrokový výpočet intervalu.",
                        "steps": [
                            {
                                "prompt": "Krok 1: SE = 12 / sqrt(36)",
                                "type": "input_numeric",
                                "answer": 2.0,
                                "tolerance": 0.01,
                            },
                            {
                                "prompt": "Krok 2: Vyber pravdivé tvrzení",
                                "type": "choice",
                                "options": {"A": "SE = 2", "B": "SE = 6"},
                                "correct": ["A"],
                            },
                        ],
                        "hints": ["Krok 1: sqrt(36)=6.", "Krok 2: 12/6=2."],
                        "explanation": "SE = 12/6 = 2.",
                    },
                    {
                        "id": "num-q5",
                        "number": 5,
                        "type": "code_task",
                        "language": "r",
                        "title": "Bisekce v R",
                        "text": "Napiš funkci bisection(f, a, b).",
                        "starter_code": "bisection <- function(f, a, b) {}\n",
                        "solution_code": (
                            "bisection <- function(f, a, b) { (a + b) / 2 }\n"
                        ),
                        "visible_tests": [
                            {
                                "name": "t1",
                                "code": "bisection(f, 1, 2)",
                                "expected": 1.4142,
                            }
                        ],
                        "hidden_tests": [
                            {
                                "name": "h1",
                                "code": "bisection(g, 0, 1)",
                                "expected": 0.7391,
                            }
                        ],
                        "hints": ["Krok 1: půl interval."],
                        "explanation": "Bisekce půlí interval.",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (num_dir / "banky" / "doplnovacka" / "01r-pojmy.json").write_text(
        json.dumps(
            {
                "title": "Doplňovačka NUM",
                "lecture": 1,
                "questions": [
                    {
                        "id": "num-r1",
                        "number": 1,
                        "type": "recall",
                        "text": "Jak se nazývá množina ℕ?",
                        "accept": ["přirozená čísla", "prirozena cisla"],
                        "explanation": "ℕ jsou přirozená čísla.",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (num_dir / "glosar" / "glosar.json").write_text(
        json.dumps(
            {
                "banks": {
                    "01-zaklady": [
                        {
                            "term": "Číslo podmíněnosti",
                            "body": "Citlivost řešení na malé perturbace vstupu.",
                        },
                        {
                            "term": "Bisekce",
                            "body": "Metoda půlení intervalu pro hledání kořene.",
                        },
                    ]
                }
            },
            ensure_ascii=False,
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
                                "name": "Newtonova metoda",
                                "latex": "x_{k+1} = x_k - \\frac{f(x_k)}{f'(x_k)}",
                                "note": "Kvadratická konvergence.",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # PAS subject
    pas_dir = subjects_root / "pas"
    (pas_dir / "banky").mkdir(parents=True, exist_ok=True)
    (pas_dir / "glosar").mkdir(parents=True, exist_ok=True)
    (pas_dir / "meta.json").write_text(
        json.dumps(
            {
                "slug": "pas",
                "code": "KI/PAS",
                "short": "PAS",
                "title": "Pravděpodobnost a statistika",
                "visibility": "public",
                "engines": ["choice", "input_numeric"],
                "tools": ["glossary", "formulas", "dist_tables"],
                "exam_config": {"count": 5, "minutes": 30, "pass_pct": 60},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pas_dir / "banky" / "01-pas.json").write_text(
        json.dumps(
            {
                "title": "01 · PAS",
                "lecture": 1,
                "questions": [
                    {
                        "id": "pas-q1",
                        "number": 1,
                        "type": "choice",
                        "text": "Jaký je medián N(0, 1)?",
                        "options": {"A": "0", "B": "1", "C": "0.5", "D": "-1"},
                        "correct": ["A"],
                        "hints": ["Symetrické kolem nuly."],
                        "explanation": "Medián N(0,1) je 0.",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Hidden admin-only draft subject
    secret_dir = subjects_root / "draft_secret"
    (secret_dir / "banky").mkdir(parents=True, exist_ok=True)
    (secret_dir / "meta.json").write_text(
        json.dumps(
            {
                "slug": "draft_secret",
                "code": "KI/SECRET",
                "short": "SEC",
                "title": "Tajný návrh předmětu",
                "visibility": "admin",
                "engines": ["choice"],
                "tools": ["formulas"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (secret_dir / "banky" / "01-secret.json").write_text(
        json.dumps(
            {
                "title": "Tajný okruh",
                "lecture": 1,
                "questions": [
                    {
                        "id": "sec-q1",
                        "number": 1,
                        "type": "choice",
                        "text": "Tajná otázka?",
                        "options": {"A": "Ano", "B": "Ne"},
                        "correct": ["A"],
                        "hints": ["Tajná nápověda"],
                        "explanation": "Tajné vysvětlení",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def engine_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolated DB, subjects tree, and FastAPI TestClient with study router mounted."""
    db_file = tmp_path / "engine_test.db"
    subjects_root = tmp_path / "subjects"
    _populate_engine_subjects(subjects_root)

    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("UNI_DB_PATH", str(db_file))
    monkeypatch.setenv("APP_SECRET", "engine-v2-test-secret-key-0123456789abcdef")
    monkeypatch.setattr(cfg, "SUBJECTS_DIR", subjects_root)
    monkeypatch.setattr(subjects_mod, "SUBJECTS_DIR", subjects_root)

    db.init_db(db_file)
    app = create_app()
    existing_paths = {getattr(r, "path", "") for r in app.routes}
    if "/api/{slug}/sources" not in existing_paths:
        app.include_router(study_router.router)

    user = db.create_user("student1", "StrongPass123!", role="user", db_path=db_file)
    admin = db.create_user("admin1", "AdminStrong123!", role="admin", db_path=db_file)

    return {
        "app": app,
        "db_path": db_file,
        "subjects_root": subjects_root,
        "user": user,
        "admin": admin,
    }


def _login_client(app: Any, username: str, password: str) -> TestClient:
    client = TestClient(app)
    resp = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    return client


# ---------------------------------------------------------------------------
# 1. FSRS, Chybovník, Leech (>= 3 lapses), and 5-minute Quick Drill
# ---------------------------------------------------------------------------


def test_fsrs_stability_difficulty_due_and_retrievability() -> None:
    t0 = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    s1 = fsrs.schedule_review(
        stability=0.0, difficulty=5.0, lapses=0, reps=0, rating=fsrs.RATING_GOOD, now=t0
    )
    assert s1.stability == pytest.approx(2.4, rel=1e-4)
    assert s1.difficulty == pytest.approx(5.0, rel=1e-4)
    assert s1.lapses == 0
    assert not s1.leech
    assert s1.due_at > "2026-11-24T12:00:00Z"

    # At elapsed == stability, retrievability R(S, S) == 0.9 by FSRS-4.5 power law.
    assert fsrs.compute_retrievability(s1.stability, s1.stability) == pytest.approx(
        0.9, abs=1e-4
    )

    # Recall after 2.4 days grows stability further.
    t1 = t0 + timedelta(days=s1.stability)
    s2 = fsrs.schedule_review(
        stability=s1.stability,
        difficulty=s1.difficulty,
        lapses=s1.lapses,
        reps=1,
        rating=fsrs.RATING_GOOD,
        elapsed_days=s1.stability,
        now=t1,
    )
    assert s2.stability > s1.stability
    assert s2.interval_days > s1.interval_days

    # Lapse drops stability sharply, raises difficulty, and increments lapses.
    t2 = t1 + timedelta(days=3.0)
    s3 = fsrs.schedule_review(
        stability=s2.stability,
        difficulty=s2.difficulty,
        lapses=s2.lapses,
        reps=2,
        rating=fsrs.RATING_AGAIN,
        elapsed_days=3.0,
        now=t2,
    )
    assert s3.stability < s2.stability
    assert s3.difficulty > s2.difficulty
    assert s3.lapses == 1
    assert s3.interval_days < 0.05  # Quick 10-minute re-queue


def test_chybovnik_leech_and_quick_drill_priority(engine_env: dict[str, Any]) -> None:
    uid = engine_env["user"]["id"]
    db_path = engine_env["db_path"]
    t0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)

    # Question num-q1 fails 3 times -> becomes LEECH (lapses == 3).
    r1 = fsrs.record_review(
        uid,
        "num",
        "num-q1",
        topic_id="01-zaklady",
        correct=False,
        now=t0,
        db_path=db_path,
    )
    assert r1["lapses"] == 1 and r1["leech"] is False
    r2 = fsrs.record_review(
        uid,
        "num",
        "num-q1",
        topic_id="01-zaklady",
        correct=False,
        now=t0 + timedelta(minutes=15),
        db_path=db_path,
    )
    assert r2["lapses"] == 2 and r2["leech"] is False
    r3 = fsrs.record_review(
        uid,
        "num",
        "num-q1",
        topic_id="01-zaklady",
        correct=False,
        now=t0 + timedelta(minutes=30),
        db_path=db_path,
    )
    assert r3["lapses"] == 3 and r3["leech"] is True

    # Question pas-q1 answered right on Nov 20 -> becomes OVERDUE by Nov 25.
    fsrs.record_review(
        uid, "pas", "pas-q1", topic_id="01-pas", correct=True, now=t0, db_path=db_path
    )

    # Question num-q2 fails once on Nov 25 -> recent mistake, not leech, due in 10 min.
    now_t = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
    fsrs.record_review(
        uid,
        "num",
        "num-q2",
        topic_id="01-zaklady",
        correct=False,
        error_reason="arith",
        now=now_t,
        db_path=db_path,
    )

    # Hidden draft mistake should NEVER be included when visible_slugs excludes it.
    fsrs.record_review(
        uid,
        "draft_secret",
        "sec-q1",
        topic_id="01-secret",
        correct=False,
        now=t0,
        db_path=db_path,
    )

    errors = fsrs.get_error_log_items(uid, subject_slug="num", db_path=db_path)
    assert [e["question_id"] for e in errors] == ["num-q1", "num-q2"]
    assert errors[0]["leech"] is True
    assert errors[1]["leech"] is False

    drill = fsrs.select_quick_drill(
        uid, ["num", "pas"], limit=5, now=now_t, db_path=db_path
    )
    ids_reasons = [
        (d["subject_slug"], d["question_id"], d["priority_reason"]) for d in drill
    ]
    assert ids_reasons[0] == ("num", "num-q1", "leech")
    assert ("pas", "pas-q1", "overdue") in ids_reasons
    assert ("num", "num-q2", "recent_mistake") in ids_reasons
    assert all(d["subject_slug"] != "draft_secret" for d in drill)


# ---------------------------------------------------------------------------
# 2. Verifiers: numeric_eps, recall_lev1, exact, choice, multi_step, code_task
# ---------------------------------------------------------------------------


def test_numeric_eps_parsing_and_tolerances() -> None:
    assert parse_numeric("3.14") == pytest.approx(3.14)
    assert parse_numeric("3,14") == pytest.approx(3.14)
    assert parse_numeric("17/12") == pytest.approx(17 / 12)
    assert parse_numeric("-3 / 4") == pytest.approx(-0.75)
    assert parse_numeric("1.5e-4") == pytest.approx(1.5e-4)
    assert parse_numeric("1,5e-4") == pytest.approx(1.5e-4)
    assert parse_numeric("\u22122,5") == pytest.approx(-2.5)
    assert parse_numeric("1/0") is None
    assert parse_numeric("abc") is None
    assert parse_numeric("1/2/3") is None

    q_frac = {"type": "input_numeric", "answer": 17 / 12, "tolerance": 1e-4}
    assert default_verifier_registry.verify(q_frac, "17/12")["correct"] is True
    assert default_verifier_registry.verify(q_frac, "1,4167")["correct"] is True
    assert default_verifier_registry.verify(q_frac, "1.42")["correct"] is False

    q_sci = {
        "type": "input_numeric",
        "answer": 1.5e-4,
        "tolerance": 1e-2,
        "tolerance_mode": "rel",
    }
    assert default_verifier_registry.verify(q_sci, "1.5e-4")["correct"] is True
    assert default_verifier_registry.verify(q_sci, "1,51e-4")["correct"] is True
    assert default_verifier_registry.verify(q_sci, "2.0e-4")["correct"] is False


def test_recall_lev1_czech_diacritics_and_typos() -> None:
    assert levenshtein_distance("cisla", "cisla") == 0
    assert levenshtein_distance("cisla", "cisl") == 1

    q = {
        "type": "recall",
        "accept": ["přirozená čísla", "celá čísla"],
        "explanation": "ℕ jsou přirozená čísla.",
    }
    # Diacritic & case folded exact match
    assert default_verifier_registry.verify(q, "Prirozena cisla")["correct"] is True
    # 1 typo (Levenshtein == 1)
    assert default_verifier_registry.verify(q, "prirozena cisl")["correct"] is True
    assert default_verifier_registry.verify(q, "prirzena cisla")["correct"] is True
    # 2 typos (Levenshtein == 2) rejected
    assert default_verifier_registry.verify(q, "prirzna cisl")["correct"] is False


def test_choice_exact_multistep_and_code_verifiers() -> None:
    q_multi = {
        "type": "choice",
        "options": {"A": "1", "B": "2", "C": "3"},
        "correct": ["A", "C"],
    }
    assert default_verifier_registry.verify(q_multi, ["c", "a"])["correct"] is True
    assert default_verifier_registry.verify(q_multi, ["A"])["correct"] is False

    q_tzi_input = {"type": "input", "answer": "100101", "solution": "37 = 100101₂"}
    assert default_verifier_registry.verify(q_tzi_input, "0b100101")["correct"] is True
    assert default_verifier_registry.verify(q_tzi_input, "100 101")["correct"] is True

    q_code = {
        "type": "code_task",
        "language": "r",
        "visible_tests": [{"name": "t1"}, {"name": "t2"}],
    }
    ok_sub = {
        "ok": True,
        "language": "r",
        "driver": "webr",
        "tests": [{"name": "t1", "passed": True}, {"name": "t2", "passed": True}],
    }
    bad_sub = {
        "ok": False,
        "language": "r",
        "driver": "webr",
        "tests": [{"name": "t1", "passed": True}, {"name": "t2", "passed": False}],
    }
    assert default_verifier_registry.verify(q_code, ok_sub)["correct"] is True
    assert default_verifier_registry.verify(q_code, bad_sub)["correct"] is False


# ---------------------------------------------------------------------------
# 3. Deterministic NUM & PAS Problem Generators + Verify Round-Trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gen_id",
    [
        "num_bisection_step_v1",
        "num_newton_iter_v1",
        "num_rounding_error_v1",
        "num_trapezoid_v1",
        "pas_zscore_prob_v1",
        "pas_sample_mean_var_v1",
        "pas_ci_halfwidth_v1",
        "pas_t_stat_v1",
    ],
)
def test_deterministic_generators_reconstruct_and_verify(gen_id: str) -> None:
    q1 = generate_question(gen_id, seed=42)
    q2 = generate_question(gen_id, seed=42)
    q3 = generate_question(gen_id, seed=99)

    assert q1 == q2
    assert q1["id"] == f"gen:{gen_id}:42"
    assert len(q1["hints"]) == 3
    assert q1["text"] != q3["text"] or q1["answer"] != q3["answer"]

    reconstructed = reconstruct_generated_question(q1["id"])
    assert reconstructed == q1

    res_ok = default_verifier_registry.verify(
        reconstructed, str(q1["answer"]).replace(".", ",")
    )
    assert res_ok["correct"] is True
    res_bad = default_verifier_registry.verify(
        reconstructed, str(float(q1["answer"]) + 100.0)
    )
    assert res_bad["correct"] is False


def test_generator_registry_listing() -> None:
    assert len(list_generators_for_subject("num")) == 4
    assert len(list_generators_for_subject("pas")) == 4
    assert default_generator_registry.get("nonexistent") is None


# ---------------------------------------------------------------------------
# 4. Subject Tools Accuracy: dist_tables, numeric_lab, automata
# ---------------------------------------------------------------------------


def test_dist_tables_pure_python_accuracy() -> None:
    # Normal distribution
    assert normal_pdf(0.0) == pytest.approx(1.0 / math.sqrt(2 * math.pi), rel=1e-9)
    assert normal_cdf(0.0) == pytest.approx(0.5, abs=1e-12)
    assert normal_cdf(1.9599639845) == pytest.approx(0.975, abs=1e-7)
    assert normal_quantile(0.975) == pytest.approx(1.9599639845, abs=1e-7)
    assert normal_quantile(0.95, mu=100.0, sigma=15.0) == pytest.approx(
        100.0 + 15.0 * 1.6448536269, abs=1e-5
    )

    # Student-t distribution
    assert t_cdf(0.0, df=5) == pytest.approx(0.5, abs=1e-12)
    assert t_quantile(0.975, df=1) == pytest.approx(12.706204736, abs=1e-5)
    assert t_quantile(0.975, df=10) == pytest.approx(2.228138852, abs=1e-5)
    assert t_quantile(0.975, df=30) == pytest.approx(2.042272456, abs=1e-5)
    assert t_cdf(2.228138852, df=10) == pytest.approx(0.975, abs=1e-6)

    # Chi-square distribution
    assert chi2_quantile(0.95, df=1) == pytest.approx(3.841458821, abs=1e-5)
    assert chi2_quantile(0.95, df=5) == pytest.approx(11.070497694, abs=1e-5)
    assert chi2_quantile(0.95, df=10) == pytest.approx(18.307038053, abs=1e-5)
    assert chi2_cdf(11.070497694, df=5) == pytest.approx(0.95, abs=1e-6)


def test_numeric_lab_bisection_and_newton_convergence() -> None:
    bis = compute_bisection("x2_minus_c", a=1.0, b=2.0, c=2.0, tol=1e-5, max_iter=25)
    assert bis["bracket_valid"] is True
    assert bis["converged"] is True
    assert bis["root"] == pytest.approx(math.sqrt(2.0), abs=1e-4)

    newt = compute_newton("x2_minus_c", x0=2.0, c=2.0, tol=1e-8, max_iter=10)
    assert newt["converged"] is True
    assert newt["root"] == pytest.approx(math.sqrt(2.0), abs=1e-7)
    assert len(newt["tangents"]) == len(newt["iterations"])


def test_automata_dfa_nfa_epsilon_and_equivalence() -> None:
    # NFA accepting binary strings ending with "01" (with epsilon transition)
    nfa = {
        "states": ["q0", "q1", "q2", "q3"],
        "alphabet": ["0", "1"],
        "start": "q0",
        "accept": ["q3"],
        "transitions": {
            "q0": {"0": ["q0", "q1"], "1": ["q0"]},
            "q1": {"": ["q2"]},
            "q2": {"1": ["q3"]},
            "q3": {},
        },
    }
    dfa_same = {
        "states": ["s0", "s1", "s2"],
        "alphabet": ["0", "1"],
        "start": "s0",
        "accept": ["s2"],
        "transitions": {
            "s0": {"0": "s1", "1": "s0"},
            "s1": {"0": "s1", "1": "s2"},
            "s2": {"0": "s1", "1": "s0"},
        },
    }
    dfa_diff = {
        "states": ["s0", "s1"],
        "alphabet": ["0", "1"],
        "start": "s0",
        "accept": ["s0"],
        "transitions": {
            "s0": {"0": "s1", "1": "s0"},
            "s1": {"0": "s0", "1": "s1"},
        },
    }

    sim_ok = simulate_automaton(nfa, "11001")
    assert sim_ok["accepted"] is True
    assert sim_ok["deterministic"] is False
    assert simulate_automaton(nfa, "11010")["accepted"] is False

    eq = are_equivalent(nfa, dfa_same)
    assert eq["equivalent"] is True
    assert eq["counterexample"] is None

    neq = are_equivalent(nfa, dfa_diff)
    assert neq["equivalent"] is False
    assert isinstance(neq["counterexample"], str)
    # Verify counterexample truly distinguishes the two automata.
    assert (
        simulate_automaton(nfa, neq["counterexample"])["accepted"]
        != simulate_automaton(dfa_diff, neq["counterexample"])["accepted"]
    )


# ---------------------------------------------------------------------------
# 5. Study API Router: Security, Hint Lock, Stripping, Round-Trips, Hidden 404
# ---------------------------------------------------------------------------


def test_hidden_subject_returns_404_across_all_study_endpoints(
    engine_env: dict[str, Any],
) -> None:
    student = _login_client(engine_env["app"], "student1", "StrongPass123!")
    admin = _login_client(engine_env["app"], "admin1", "AdminStrong123!")

    # Non-admin MUST receive 404 everywhere for hidden visibility=="admin" subject.
    assert student.get("/api/draft_secret/sources").status_code == 404
    assert student.get("/api/draft_secret/questions").status_code == 404
    assert student.get("/api/draft_secret/hint/sec-q1?step=1").status_code == 404
    assert student.get("/api/draft_secret/tools/formulas").status_code == 404
    assert (
        student.post(
            "/api/draft_secret/verify",
            json={"question_id": "sec-q1", "answer": "A"},
        ).status_code
        == 404
    )
    assert (
        student.post(
            "/api/draft_secret/exam/finish",
            json={"mode": "exam", "score": 1, "total": 1},
        ).status_code
        == 404
    )
    assert student.get("/api/drill/quick?subject=draft_secret").status_code == 404

    # Unknown subject also 404.
    assert student.get("/api/unknown_slug/sources").status_code == 404

    # Admin CAN inspect draft_secret.
    assert admin.get("/api/draft_secret/sources").status_code == 200


def test_question_secret_stripping_and_server_side_hint_lock(
    engine_env: dict[str, Any],
) -> None:
    client = _login_client(engine_env["app"], "student1", "StrongPass123!")

    # Practice mode strips answers/solutions/hidden_tests, keeps hints.
    r_prac = client.get("/api/num/questions?mode=practice")
    assert r_prac.status_code == 200
    data_prac = r_prac.json()
    assert data_prac["hints_locked"] is False
    for q in data_prac["questions"]:
        for forbidden in (
            "correct",
            "answer",
            "accept",
            "solution",
            "solution_code",
            "hidden_tests",
            "explanation",
        ):
            assert forbidden not in q
        if q["type"] == "multi_step":
            for step in q["steps"]:
                assert "answer" not in step
                assert "correct" not in step

    q1_prac = next(q for q in data_prac["questions"] if q["id"] == "num-q1")
    assert q1_prac["hints_count"] == 3
    assert len(q1_prac["hints"]) == 3

    # Exam & sudden_death modes ALSO strip hints/hint completely on the server.
    for locked_mode in ("exam", "sudden_death"):
        r_lock = client.get(f"/api/num/questions?mode={locked_mode}")
        assert r_lock.status_code == 200
        payload = r_lock.json()
        assert payload["hints_locked"] is True
        for q in payload["questions"]:
            assert "hints" not in q
            assert "hint" not in q
            assert q["hints_locked"] is True
            assert q["hints_count"] == 0

        # Progressive hint endpoint strictly returns 403 in locked modes!
        h_lock = client.get(f"/api/num/hint/num-q1?step=1&mode={locked_mode}")
        assert h_lock.status_code == 403

    # Progressive hints 1 -> 2 -> 3 work in practice mode.
    h1 = client.get("/api/num/hint/num-q1?step=1&mode=practice")
    h2 = client.get("/api/num/hint/num-q1?step=2&mode=practice")
    h3 = client.get("/api/num/hint/num-q1?step=3&mode=practice")
    assert h1.status_code == 200 and h1.json()["has_next"] is True
    assert h2.status_code == 200 and h2.json()["step"] == 2
    assert h3.status_code == 200 and h3.json()["has_next"] is False


def test_api_generated_roundtrip_verify_fsrs_and_exam_finish(
    engine_env: dict[str, Any],
) -> None:
    client = _login_client(engine_env["app"], "student1", "StrongPass123!")

    # Fetch deterministic generated Newton question.
    r_gen = client.get("/api/num/questions?generator_id=num_newton_iter_v1&seed=1337")
    assert r_gen.status_code == 200
    gen_q = r_gen.json()["questions"][0]
    assert "answer" not in gen_q
    assert gen_q["id"] == "gen:num_newton_iter_v1:1337"

    # Reconstruct expected answer to test verify round-trip.
    expected = generate_question("num_newton_iter_v1", seed=1337)["answer"]
    v_ok = client.post(
        "/api/num/verify",
        json={
            "question_id": gen_q["id"],
            "generator_id": "num_newton_iter_v1",
            "seed": 1337,
            "answer": str(expected).replace(".", ","),
        },
    )
    assert v_ok.status_code == 200
    body = v_ok.json()
    assert body["correct"] is True
    assert body["fsrs"]["stability"] > 0
    assert body["daily"]["done"] >= 1

    # Verify wrong answers 3x to trigger leech & quick drill prioritization.
    for _ in range(3):
        v_err = client.post(
            "/api/num/verify",
            json={"question_id": "num-q1", "answer": "A"},
        )
        assert v_err.status_code == 200
        assert v_err.json()["correct"] is False
    assert v_err.json()["leech"] is True

    # Quick drill endpoint surfaces the leech first and strips answers.
    r_drill = client.get("/api/drill/quick?limit=5")
    assert r_drill.status_code == 200
    drill_data = r_drill.json()
    assert drill_data["leech_total"] >= 1
    assert drill_data["questions"][0]["id"] == "num-q1"
    assert drill_data["questions"][0]["leech"] is True
    assert "correct" not in drill_data["questions"][0]

    # Exam finish saves to user_scores.
    r_fin = client.post(
        "/api/num/exam/finish",
        json={"mode": "exam", "score": 3, "total": 4, "duration_sec": 120},
    )
    assert r_fin.status_code == 200
    fin_data = r_fin.json()
    assert fin_data["saved"] is True
    assert fin_data["pct"] == 75
    assert fin_data["passed"] is True

    # Tools endpoints return expected structured data.
    assert client.get("/api/num/tools/glossary?q=podminenosti").json()["total"] == 1
    assert client.get("/api/num/tools/formulas?q=Newton").json()["total"] == 1
    assert (
        client.get("/api/num/tools/numeric_lab?method=newton").json()["converged"]
        is True
    )
    assert "tables" in client.get("/api/pas/tools/dist_tables").json()
    aut_post = client.post(
        "/api/num/tools/automata",
        json={
            "action": "simulate",
            "word": "0011",
            "automaton": {
                "states": ["q0", "q1"],
                "alphabet": ["0", "1"],
                "start": "q0",
                "accept": ["q0"],
                "transitions": {
                    "q0": {"0": "q1", "1": "q0"},
                    "q1": {"0": "q0", "1": "q1"},
                },
            },
        },
    )
    assert aut_post.status_code == 200
    assert aut_post.json()["accepted"] is True


def test_verify_csrf_enforced_when_not_testing(
    engine_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TESTING", "0")
    client = TestClient(engine_env["app"])
    resp = client.post(
        "/api/num/verify",
        json={"question_id": "num-q1", "answer": "B"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 6. Frontend Strict CSP / Trusted Types Static Audit
# ---------------------------------------------------------------------------


def test_engine_frontend_js_strict_trusted_types_audit() -> None:
    js_files = list(
        (REPO_ROOT / "ui" / "public" / "js" / "engine").rglob("*.js")
    ) + list((REPO_ROOT / "ui" / "public" / "js" / "tools").rglob("*.js"))
    assert len(js_files) >= 12
    forbidden = re.compile(
        r"\.\s*innerHTML\b|\.\s*outerHTML\b|insertAdjacentHTML\s*\(|"
        r"document\.write\s*\(|new\s+DOMParser\b|\beval\s*\(|new\s+Function\s*\(|"
        r"\.style\.[a-zA-Z]|setAttribute\(\s*['\"]style['\"]"
    )
    for path in js_files:
        text = path.read_text(encoding="utf-8")
        # Strip comments so docblocks saying "zero innerHTML" don't false-positive.
        code_only = re.sub(r"/\*[\s\S]*?\*/|//.*", "", text)
        match = forbidden.search(code_only)
        assert match is None, f"Forbidden DOM/CSP pattern in {path}: {match.group(0)}"
