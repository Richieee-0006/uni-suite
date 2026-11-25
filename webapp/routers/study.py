"""FastAPI router for UNI Engine v2 study endpoints, hints, tools, and quick drill."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

import db
from webapp import config as cfg
from webapp import subjects as subjects_mod
from webapp.csrf import verify_csrf
from webapp.engine import fsrs
from webapp.generators import (
    SeededRNG,
    generate_question,
    list_generators_for_subject,
    parse_generated_qid,
    reconstruct_generated_question,
)
from webapp.sessions import get_current_user, is_admin_user
from webapp.tools import (
    SUPPORTED_TOOLS,
    are_equivalent,
    get_automata_tool_data,
    get_dist_tables_data,
    get_formulas_tool_data,
    get_glossary_tool_data,
    get_numeric_lab_data,
    simulate_automaton,
)
from webapp.verifiers import default_verifier_registry

router = APIRouter(tags=["study"])

ALLOWED_MODES = frozenset(
    {
        "practice",
        "blocks",
        "study_then_test",
        "exam",
        "sudden_death",
        "fsrs_review",
        "error_log",
        "quick_drill",
    }
)
HINT_LOCKED_MODES = frozenset({"exam", "sudden_death"})

# Fields strictly stripped from every question sent to the client.
_SECRET_QUESTION_KEYS = frozenset(
    {
        "correct",
        "answer",
        "accept",
        "solution",
        "solution_code",
        "hidden_tests",
        "explanation",
        "target_automaton",
    }
)


def _subjects_root() -> Path:
    return cfg.SUBJECTS_DIR


def _require_visible_local_subject(
    slug: str,
    request: Request,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """Resolve visible local subject or raise 404 (anti-enumeration)."""
    user = get_current_user(request)
    uid = user["id"] if (user and user.get("is_authenticated")) else None
    admin = is_admin_user(user)
    detail = subjects_mod.get_subject_detail(
        slug,
        user_id=uid,
        is_admin=admin,
        subjects_dir=_subjects_root(),
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Not Found")
    subject, topics, _ = detail
    if subject.get("kind") != "local":
        raise HTTPException(status_code=404, detail="Not Found")
    return subject, topics, user


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _load_subject_questions(slug: str) -> list[dict[str, Any]]:
    """Load all bank and doplnovacka (recall) questions for a local subject."""
    subject_dir = _subjects_root() / slug
    items: list[dict[str, Any]] = []

    sources = [
        (subject_dir / "banky", "bank"),
        (subject_dir / "banky" / "doplnovacka", "doplnovacka"),
    ]
    for directory, kind in sources:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            questions_raw = payload.get("questions")
            if not isinstance(questions_raw, list):
                continue
            stem = path.stem
            bank_title = str(payload.get("title") or stem)
            for idx, q in enumerate(questions_raw, start=1):
                if not isinstance(q, dict):
                    continue
                entry = dict(q)
                qid = str(entry.get("id") or f"{stem}:{entry.get('number', idx)}")
                entry["id"] = qid
                entry["topic_id"] = str(entry.get("topic_id") or stem)
                entry["topic_title"] = bank_title
                entry["source_kind"] = kind
                entry["subject_slug"] = slug
                if not entry.get("type"):
                    entry["type"] = "recall" if kind == "doplnovacka" else "choice"
                items.append(entry)
    return items


def _extract_hints(question: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    raw_hints = question.get("hints")
    if isinstance(raw_hints, list):
        for item in raw_hints:
            text = str(item).strip()
            if text:
                hints.append(text)
    if not hints and isinstance(question.get("hint"), str) and question["hint"].strip():
        hints.append(question["hint"].strip())
    return hints[:3]


def _sanitize_step(step: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in step.items():
        if k in _SECRET_QUESTION_KEYS:
            continue
        clean[k] = v
    if str(step.get("type") or "").lower() == "choice":
        correct = step.get("correct")
        clean["multi"] = isinstance(correct, list) and len(correct) > 1
    return clean


def sanitize_question_for_client(question: dict[str, Any], mode: str) -> dict[str, Any]:
    """Strip answers/solutions/hidden_tests; in exam/sudden_death also strip hints."""
    locked = mode in HINT_LOCKED_MODES
    clean: dict[str, Any] = {}

    for key, val in question.items():
        if key in _SECRET_QUESTION_KEYS:
            continue
        if locked and key in {"hints", "hint"}:
            continue
        if key == "steps" and isinstance(val, list):
            clean["steps"] = [
                _sanitize_step(step) for step in val if isinstance(step, dict)
            ]
            continue
        if key == "languages" and isinstance(val, dict):
            clean_langs: dict[str, dict[str, Any]] = {}
            for lang_code, variant in val.items():
                if isinstance(variant, dict):
                    clean_langs[str(lang_code)] = {
                        vk: vv
                        for vk, vv in variant.items()
                        if vk not in _SECRET_QUESTION_KEYS
                    }
            clean["languages"] = clean_langs
            continue
        clean[key] = val

    qtype = str(question.get("type") or "choice").strip().lower()
    if qtype == "choice":
        correct = question.get("correct")
        clean["multi"] = isinstance(correct, list) and len(correct) > 1

    hints = _extract_hints(question)
    if locked:
        clean.pop("hints", None)
        clean.pop("hint", None)
        clean["hints_locked"] = True
        clean["hints_count"] = 0
    else:
        clean["hints_locked"] = False
        clean["hints_count"] = len(hints)
        if hints:
            clean["hints"] = hints

    return clean


def _instantiate_if_parameterized(
    question: dict[str, Any],
    seed_base: int,
    index: int,
) -> dict[str, Any]:
    """If a bank question declares a generator block, instantiate deterministically."""
    gen_meta = question.get("generator")
    if not isinstance(gen_meta, dict) or not isinstance(gen_meta.get("id"), str):
        return question
    gen_id = gen_meta["id"].strip()
    if not gen_id:
        return question
    raw_seed = gen_meta.get("seed")
    seed = int(raw_seed) if isinstance(raw_seed, int) else (seed_base + index * 97)
    params = (
        gen_meta.get("params") if isinstance(gen_meta.get("params"), dict) else None
    )
    try:
        generated = generate_question(gen_id, seed, params=params)
        generated["subject_slug"] = question.get("subject_slug") or ""
        return generated
    except KeyError:
        return question


def _deterministic_shuffle(
    items: list[dict[str, Any]], seed: int
) -> list[dict[str, Any]]:
    result = list(items)
    rng = SeededRNG(seed=seed, salt="study-shuffle")
    for i in range(len(result) - 1, 0, -1):
        j = rng.randint(0, i)
        result[i], result[j] = result[j], result[i]
    return result


def _type_matches(question_type: str, requested_type: str) -> bool:
    qt = question_type.strip().lower()
    rt = requested_type.strip().lower()
    if rt in {"", "all"}:
        return True
    if rt == "input_exact":
        return qt in {"input_exact", "input"}
    return qt == rt


def _resolve_question_by_id(
    slug: str,
    question_id: str,
    *,
    generator_id: str | None = None,
    seed: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Locate a question from deterministic generators or subject JSON banks."""
    if generator_id is not None and seed is not None:
        reconstructed = reconstruct_generated_question(
            question_id,
            generator_id=generator_id,
            seed=seed,
            params=params,
        )
        if reconstructed is not None:
            # Ensure generator belongs to requested subject (no cross-subject leak).
            subject_gen_ids = {g["id"] for g in list_generators_for_subject(slug)}
            if generator_id in subject_gen_ids:
                reconstructed["subject_slug"] = slug
                return reconstructed
            return None

    parsed = parse_generated_qid(question_id)
    if parsed is not None:
        gen_id, parsed_seed = parsed
        subject_gen_ids = {g["id"] for g in list_generators_for_subject(slug)}
        if gen_id not in subject_gen_ids:
            return None
        reconstructed = reconstruct_generated_question(
            question_id,
            generator_id=gen_id,
            seed=parsed_seed,
            params=params,
        )
        if reconstructed is not None:
            reconstructed["subject_slug"] = slug
        return reconstructed

    for item in _load_subject_questions(slug):
        if item["id"] == question_id:
            return item
    return None


@router.get("/api/{slug}/sources")
async def api_subject_sources(slug: str, request: Request) -> dict[str, Any]:
    """Return subject banks, topics, mastery, error/due/leech counts, and generators."""
    subject, topics, user = _require_visible_local_subject(slug, request)
    uid = user["id"] if (user and user.get("is_authenticated")) else None
    error_count, due_count = db.get_subject_error_and_due_counts(uid, subject["slug"])
    leech_count = 0
    if uid is not None:
        leech_count = sum(
            1
            for row in fsrs.get_error_log_items(uid, subject_slug=subject["slug"])
            if row["leech"]
        )

    return {
        "slug": subject["slug"],
        "code": subject["code"],
        "short": subject["short"],
        "title": subject["title"],
        "description": subject["description"],
        "engines": subject["engines"],
        "tools": subject["tools"],
        "sandbox": subject["sandbox"],
        "exam_config": subject["exam_config"],
        "question_count": subject["question_count"],
        "recall_count": subject["recall_count"],
        "total_items": subject["total_items"],
        "mastery_level": subject["mastery_level"],
        "mastery_label": subject["mastery_label"],
        "progress_pct": subject["progress_pct"],
        "readiness_pct": subject["readiness_pct"],
        "error_count": error_count,
        "due_count": due_count,
        "leech_count": leech_count,
        "banks": topics,
        "topics": topics,
        "generators": list_generators_for_subject(subject["slug"]),
    }


@router.get("/api/{slug}/questions")
async def api_subject_questions(
    slug: str,
    request: Request,
    mode: str = Query("practice"),
    topic: str = Query(""),
    type: str = Query("all"),
    limit: int = Query(0, ge=0, le=100),
    seed: int | None = Query(None),
    generator_id: str = Query(""),
) -> dict[str, Any]:
    """Fetch study questions with server-side answer and exam-mode hint stripping."""
    clean_mode = mode.strip().lower()
    if clean_mode not in ALLOWED_MODES:
        raise HTTPException(status_code=400, detail=f"Neplatný studijní režim: {mode}")

    subject, _, user = _require_visible_local_subject(slug, request)
    uid = user["id"] if (user and user.get("is_authenticated")) else None
    clean_slug = subject["slug"]

    if seed is None:
        seed_bytes = hashlib.sha256(
            f"{clean_slug}:{clean_mode}:{topic}".encode()
        ).digest()
        effective_seed = int.from_bytes(seed_bytes[:4], "big")
    else:
        effective_seed = int(seed)

    # Explicit deterministic generator request.
    clean_gen_id = generator_id.strip()
    if (
        clean_gen_id
        or topic.strip().lower() == "generated"
        or type.strip().lower() == "generated"
    ):
        subject_gens = list_generators_for_subject(clean_slug)
        if clean_gen_id:
            if not any(g["id"] == clean_gen_id for g in subject_gens):
                raise HTTPException(status_code=404, detail="Not Found")
            count = limit if limit > 0 else 1
            raw_list = [
                generate_question(clean_gen_id, effective_seed + i)
                for i in range(count)
            ]
        else:
            count = limit if limit > 0 else max(4, len(subject_gens))
            raw_list = []
            if subject_gens:
                for i in range(count):
                    g = subject_gens[i % len(subject_gens)]
                    raw_list.append(generate_question(g["id"], effective_seed + i * 31))
        sanitized = [sanitize_question_for_client(q, clean_mode) for q in raw_list]
        return {
            "slug": clean_slug,
            "mode": clean_mode,
            "seed": effective_seed,
            "hints_locked": clean_mode in HINT_LOCKED_MODES,
            "total": len(sanitized),
            "questions": sanitized,
        }

    all_questions = _load_subject_questions(clean_slug)
    by_id = {q["id"]: q for q in all_questions}
    topic_filter = topic.strip()
    type_filter = type.strip().lower()

    if clean_mode == "fsrs_review":
        selected: list[dict[str, Any]] = []
        if uid is not None:
            for row in fsrs.get_due_fsrs_items(uid, subject_slug=clean_slug):
                q = by_id.get(str(row["question_id"]))
                if q is not None:
                    enriched = dict(q)
                    enriched["fsrs_meta"] = {
                        "stability": row["stability"],
                        "difficulty": row["difficulty"],
                        "due_at": row["due_at"],
                        "lapses": row["lapses"],
                        "leech": row["leech"],
                    }
                    selected.append(enriched)
        pool = selected
    elif clean_mode == "error_log":
        selected = []
        if uid is not None:
            for row in fsrs.get_error_log_items(uid, subject_slug=clean_slug):
                q = by_id.get(str(row["question_id"]))
                if q is not None:
                    enriched = dict(q)
                    enriched["error_meta"] = {
                        "lapses": row["lapses"],
                        "wrong_count": row["wrong_count"],
                        "leech": row["leech"],
                        "last_error_reason": row.get("last_error_reason"),
                    }
                    selected.append(enriched)
        pool = selected
    elif clean_mode == "quick_drill":
        drill_limit = limit if limit > 0 else 10
        picked_ids: set[str] = set()
        pool = []
        if uid is not None:
            for row in fsrs.select_quick_drill(
                uid, [clean_slug], subject_slug=clean_slug, limit=drill_limit
            ):
                q = by_id.get(str(row["question_id"]))
                if q is not None:
                    enriched = dict(q)
                    enriched["priority_reason"] = row["priority_reason"]
                    enriched["leech"] = row["leech"]
                    pool.append(enriched)
                    picked_ids.add(q["id"])
        if len(pool) < drill_limit:
            for q in _deterministic_shuffle(all_questions, effective_seed):
                if q["id"] not in picked_ids:
                    enriched = dict(q)
                    enriched["priority_reason"] = "practice"
                    enriched["leech"] = False
                    pool.append(enriched)
                    picked_ids.add(q["id"])
                if len(pool) >= drill_limit:
                    break
    else:
        pool = list(all_questions)

    if topic_filter:
        pool = [q for q in pool if str(q.get("topic_id") or "") == topic_filter]
    if type_filter and type_filter != "all":
        pool = [q for q in pool if _type_matches(str(q.get("type") or ""), type_filter)]

    if clean_mode in {"exam", "sudden_death"}:
        pool = _deterministic_shuffle(pool, effective_seed)

    effective_limit = limit
    if effective_limit <= 0:
        if clean_mode == "exam":
            effective_limit = int(subject["exam_config"].get("count", 15))
        elif clean_mode == "blocks":
            effective_limit = 10
        elif clean_mode == "quick_drill":
            effective_limit = 10
    if effective_limit > 0:
        pool = pool[:effective_limit]

    sanitized_out = [
        sanitize_question_for_client(
            _instantiate_if_parameterized(q, effective_seed, idx),
            clean_mode,
        )
        for idx, q in enumerate(pool)
    ]

    return {
        "slug": clean_slug,
        "mode": clean_mode,
        "seed": effective_seed,
        "hints_locked": clean_mode in HINT_LOCKED_MODES,
        "exam_config": subject["exam_config"] if clean_mode == "exam" else None,
        "total": len(sanitized_out),
        "questions": sanitized_out,
    }


@router.post("/api/{slug}/verify", dependencies=[Depends(verify_csrf)])
async def api_verify_answer(slug: str, request: Request) -> dict[str, Any]:
    """Verify answer, update FSRS/Chybovník + daily_progress for logged-in users."""
    subject, _, user = _require_visible_local_subject(slug, request)
    clean_slug = subject["slug"]

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Neplatný JSON požadavek.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Požadavek musí být JSON objekt.")

    question_id = str(body.get("question_id") or "").strip()
    if not question_id:
        raise HTTPException(status_code=400, detail="Chybí question_id.")

    generator_id = (
        str(body["generator_id"]).strip()
        if isinstance(body.get("generator_id"), str) and body["generator_id"].strip()
        else None
    )
    raw_seed = body.get("seed")
    seed_val = (
        int(raw_seed)
        if isinstance(raw_seed, int) and not isinstance(raw_seed, bool)
        else None
    )
    gen_params = body.get("params") if isinstance(body.get("params"), dict) else None

    question = _resolve_question_by_id(
        clean_slug,
        question_id,
        generator_id=generator_id,
        seed=seed_val,
        params=gen_params,
    )
    if question is None:
        raise HTTPException(status_code=404, detail="Otázka nenalezena.")

    result = default_verifier_registry.verify(question, body.get("answer"))
    is_correct = bool(result["correct"])

    raw_rating = body.get("rating")
    rating_val = (
        int(raw_rating)
        if isinstance(raw_rating, int)
        and not isinstance(raw_rating, bool)
        and raw_rating in {1, 2, 3, 4}
        else None
    )
    error_reason = (
        str(body.get("error_reason") or "").strip()[:240] or None
        if not is_correct
        else None
    )

    fsrs_payload: dict[str, Any] | None = None
    leech = False
    daily: dict[str, int] | None = None

    if user and user.get("is_authenticated") and user.get("id") is not None:
        recorded = fsrs.record_review(
            int(user["id"]),
            clean_slug,
            question["id"],
            topic_id=str(question.get("topic_id") or ""),
            correct=is_correct,
            rating=rating_val,
            error_reason=error_reason,
        )
        leech = bool(recorded["leech"])
        daily = recorded["daily"]
        fsrs_payload = {
            "stability": recorded["stability"],
            "difficulty": recorded["difficulty"],
            "due_at": recorded["due_at"],
            "interval_days": recorded["interval_days"],
            "retrievability": recorded["retrievability"],
            "lapses": recorded["lapses"],
            "leech": leech,
        }

    return {
        "question_id": question["id"],
        "correct": is_correct,
        "explanation": result["explanation"],
        "correct_display": result["correct_display"],
        "details": result.get("details", {}),
        "fsrs": fsrs_payload,
        "leech": leech,
        "daily": daily,
    }


@router.post("/api/{slug}/exam/finish", dependencies=[Depends(verify_csrf)])
async def api_exam_finish(slug: str, request: Request) -> dict[str, Any]:
    """Persist exam/sudden_death score to user_scores for authenticated users."""
    subject, _, user = _require_visible_local_subject(slug, request)
    clean_slug = subject["slug"]

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Neplatný JSON požadavek.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Požadavek musí být JSON objekt.")

    mode = str(body.get("mode") or "exam").strip().lower()
    if mode not in ALLOWED_MODES:
        raise HTTPException(status_code=400, detail="Neplatný režim.")

    raw_score = body.get("score")
    raw_total = body.get("total")
    raw_dur = body.get("duration_sec", 0)
    if (
        not isinstance(raw_score, int)
        or isinstance(raw_score, bool)
        or not isinstance(raw_total, int)
        or isinstance(raw_total, bool)
        or raw_total < 0
        or raw_score < 0
        or raw_score > raw_total
    ):
        raise HTTPException(
            status_code=400, detail="Neplatné skóre nebo celkový počet."
        )

    duration_sec = (
        int(raw_dur)
        if isinstance(raw_dur, int) and not isinstance(raw_dur, bool) and raw_dur >= 0
        else 0
    )
    pct = round(100 * raw_score / raw_total) if raw_total > 0 else 0
    pass_pct = int(subject["exam_config"].get("pass_pct", 60))
    passed = pct >= pass_pct

    if user and user.get("is_authenticated") and user.get("id") is not None:
        saved_row = fsrs.save_exam_score(
            int(user["id"]),
            clean_slug,
            mode,
            raw_score,
            raw_total,
            duration_sec,
        )
        return {
            "saved": True,
            "id": saved_row["id"],
            "subject_slug": clean_slug,
            "mode": mode,
            "score": raw_score,
            "total": raw_total,
            "pct": pct,
            "passed": passed,
            "duration_sec": duration_sec,
        }

    return {
        "saved": False,
        "id": None,
        "subject_slug": clean_slug,
        "mode": mode,
        "score": raw_score,
        "total": raw_total,
        "pct": pct,
        "passed": passed,
        "duration_sec": duration_sec,
    }


@router.get("/api/{slug}/hint/{qid}")
async def api_progressive_hint(
    slug: str,
    qid: str,
    request: Request,
    step: int = Query(1, ge=1, le=3),
    mode: str = Query("practice"),
    generator_id: str = Query(""),
    seed: int | None = Query(None),
) -> dict[str, Any]:
    """Return progressive hint step 1..3; locked with HTTP 403 in exam/sudden_death."""
    subject, _, _ = _require_visible_local_subject(slug, request)
    clean_mode = mode.strip().lower()
    if clean_mode in HINT_LOCKED_MODES:
        raise HTTPException(
            status_code=403,
            detail="Nápovědy jsou v režimu zkoušky a náhlé smrti uzamčeny.",
        )

    question = _resolve_question_by_id(
        subject["slug"],
        qid,
        generator_id=generator_id.strip() or None,
        seed=seed,
    )
    if question is None:
        raise HTTPException(status_code=404, detail="Otázka nenalezena.")

    hints = _extract_hints(question)
    if not hints:
        return {
            "question_id": question["id"],
            "step": step,
            "total_steps": 0,
            "hint": None,
            "has_next": False,
            "locked": False,
        }

    idx = min(step, len(hints)) - 1
    return {
        "question_id": question["id"],
        "step": idx + 1,
        "total_steps": len(hints),
        "hint": hints[idx],
        "has_next": (idx + 1) < len(hints),
        "locked": False,
    }


@router.get("/api/{slug}/tools/{tool_name}")
async def api_subject_tool_get(
    slug: str,
    tool_name: str,
    request: Request,
    q: str = Query(""),
    bank: str = Query(""),
    method: str = Query("bisection"),
    preset: str = Query(""),
    a: float | None = Query(None),
    b: float | None = Query(None),
    x0: float | None = Query(None),
    c: float = Query(2.0),
    tol: float = Query(1e-6),
    max_iter: int = Query(10, ge=1, le=40),
    dist: str = Query("normal"),
    mu: float = Query(0.0),
    sigma: float = Query(1.0),
    df: int = Query(10, ge=1, le=200),
    alpha: float = Query(0.05),
    tail: str = Query("two"),
    x_value: float | None = Query(None),
    include_tables: bool = Query(True),
    word: str | None = Query(None),
) -> dict[str, Any]:
    """Serve subject tool data (glossary, formulas, numeric_lab, dist, automata)."""
    subject, _, _ = _require_visible_local_subject(slug, request)
    clean_tool = tool_name.strip().lower()
    if clean_tool not in SUPPORTED_TOOLS:
        raise HTTPException(status_code=404, detail="Not Found")

    if clean_tool == "glossary":
        return get_glossary_tool_data(
            subject["slug"], query=q, bank=bank, subjects_dir=_subjects_root()
        )
    if clean_tool == "formulas":
        return get_formulas_tool_data(
            subject["slug"], query=q, subjects_dir=_subjects_root()
        )
    if clean_tool == "numeric_lab":
        return get_numeric_lab_data(
            method=method,
            preset=preset or "x2_minus_c",
            a=a,
            b=b,
            x0=x0,
            c=c,
            tol=tol,
            max_iter=max_iter,
        )
    if clean_tool == "dist_tables":
        return get_dist_tables_data(
            dist=dist,
            mu=mu,
            sigma=sigma,
            df=df,
            alpha=alpha,
            tail=tail,
            x_value=x_value,
            include_tables=include_tables,
        )
    return get_automata_tool_data(preset_id=preset, word=word)


@router.post("/api/{slug}/tools/automata", dependencies=[Depends(verify_csrf)])
async def api_subject_tool_automata_post(slug: str, request: Request) -> dict[str, Any]:
    """Simulate a custom DFA/NFA or test equivalence between two automata."""
    _require_visible_local_subject(slug, request)

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Neplatný JSON požadavek.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Požadavek musí být JSON objekt.")

    action = str(body.get("action") or "simulate").strip().lower()
    try:
        if action == "equivalence":
            return {
                "tool": "automata",
                "action": "equivalence",
                **are_equivalent(
                    body.get("automaton_a") or {}, body.get("automaton_b") or {}
                ),
            }
        spec = body.get("automaton") or body.get("spec") or {}
        word = str(body.get("word") if body.get("word") is not None else "")
        return {
            "tool": "automata",
            "action": "simulate",
            **simulate_automaton(spec, word),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/drill/quick")
async def api_quick_drill(
    request: Request,
    limit: int = Query(10, ge=1, le=30),
    subject: str = Query(""),
) -> dict[str, Any]:
    """Cross-subject 5-minute weak-spot drill from Chybovník + due FSRS cards."""
    user = get_current_user(request)
    uid = user["id"] if (user and user.get("is_authenticated")) else None
    admin = is_admin_user(user)

    visible_subjects = [
        s
        for s in subjects_mod.load_subjects(
            user_id=uid,
            is_admin=admin,
            subjects_dir=_subjects_root(),
        )
        if s.get("kind") == "local"
    ]
    visible_by_slug = {s["slug"]: s for s in visible_subjects}
    visible_slugs = list(visible_by_slug.keys())

    filter_slug = subject.strip().lower() or None
    if filter_slug is not None and filter_slug not in visible_by_slug:
        raise HTTPException(status_code=404, detail="Not Found")

    prioritized = fsrs.select_quick_drill(
        uid,
        visible_slugs,
        subject_slug=filter_slug,
        limit=limit,
    )

    question_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def _get_map(s_slug: str) -> dict[str, dict[str, Any]]:
        if s_slug not in question_cache:
            question_cache[s_slug] = {
                q["id"]: q for q in _load_subject_questions(s_slug)
            }
        return question_cache[s_slug]

    items: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    leech_total = 0
    overdue_total = 0
    weak_total = 0

    for row in prioritized:
        s_slug = str(row["subject_slug"])
        qid = str(row["question_id"])
        q = _get_map(s_slug).get(qid)
        if q is None:
            parsed = parse_generated_qid(qid)
            if parsed is not None:
                q = reconstruct_generated_question(qid)
        if q is None:
            continue
        sanitized = sanitize_question_for_client(q, "quick_drill")
        sanitized["subject_slug"] = s_slug
        sanitized["subject_code"] = visible_by_slug[s_slug]["code"]
        sanitized["priority_reason"] = row["priority_reason"]
        sanitized["lapses"] = int(row.get("lapses", 0))
        sanitized["leech"] = bool(row.get("leech", False))
        items.append(sanitized)
        seen_keys.add((s_slug, qid))
        weak_total += 1
        if sanitized["leech"]:
            leech_total += 1
        if row["priority_reason"] == "overdue":
            overdue_total += 1

    # Backfill remaining slots with practice questions from visible subjects.
    target_slugs = [filter_slug] if filter_slug else visible_slugs
    if len(items) < limit:
        for s_slug in target_slugs:
            for q in _load_subject_questions(s_slug):
                key = (s_slug, q["id"])
                if key in seen_keys:
                    continue
                sanitized = sanitize_question_for_client(q, "quick_drill")
                sanitized["subject_slug"] = s_slug
                sanitized["subject_code"] = visible_by_slug[s_slug]["code"]
                sanitized["priority_reason"] = "practice"
                sanitized["lapses"] = 0
                sanitized["leech"] = False
                items.append(sanitized)
                seen_keys.add(key)
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

    return {
        "mode": "quick_drill",
        "total": len(items),
        "weak_total": weak_total,
        "leech_total": leech_total,
        "overdue_total": overdue_total,
        "questions": items,
    }
