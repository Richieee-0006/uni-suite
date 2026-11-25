"""Declarative subject loader, role-aware projection, mastery, and catalog search."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import db
from webapp.config import SUBJECTS_DIR

SLUG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
ALLOWED_VISIBILITIES = frozenset({"public", "admin"})
BINARY_PREVIEW_LIMIT = 24

MASTERY_LABELS: dict[str, str] = {
    "unstarted": "Neprobráno",
    "familiar": "Seznámeno",
    "proficient": "Jisté",
    "mastered": "Zvládnuto",
}

DEFAULT_SITE: dict[str, str] = {
    "title": "UNI Suite",
    "domain": "uni.rutterle.eu",
    "subtitle": "Studijní trenažéry a sandbox pro předměty KI UJEP",
}

DEFAULT_EXAM_CONFIG: dict[str, int] = {
    "count": 15,
    "minutes": 30,
    "pass_pct": 60,
}

_STUDY_MODES_SEARCH = (
    ("Procvičování", "Volné procvičování s okamžitou zpětnou vazbou", ""),
    (
        "FSRS opakování",
        "Adaptivní opakování podle křivky zapomínání",
        "?mode=fsrs_review",
    ),
    ("Chybovník", "Cílený trénink chybně zodpovězených otázek", "?mode=error_log"),
    ("Ostrý test", "Simulace zkoušky na čas bez nápověd", "?mode=exam"),
)


def _read_json_file(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def load_registry(subjects_dir: Path | None = None) -> dict[str, Any]:
    """Load subjects/registry.json with safe defaults."""
    root = subjects_dir if subjects_dir is not None else SUBJECTS_DIR
    data = _read_json_file(root / "registry.json")
    if not isinstance(data, dict):
        return {"site": dict(DEFAULT_SITE), "subjects": []}
    site_raw = data.get("site")
    site = dict(DEFAULT_SITE)
    if isinstance(site_raw, dict):
        for k in ("title", "domain", "subtitle"):
            if isinstance(site_raw.get(k), str) and site_raw[k].strip():
                site[k] = site_raw[k].strip()
    entries = data.get("subjects")
    return {
        "site": site,
        "subjects": entries if isinstance(entries, list) else [],
    }


def load_site_meta(subjects_dir: Path | None = None) -> dict[str, str]:
    """Return site title, domain, and subtitle from registry.json."""
    return load_registry(subjects_dir)["site"]


def _scan_bank_files(directory: Path, kind: str) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    if not directory.is_dir():
        return topics

    for path in sorted(directory.glob("*.json")):
        payload = _read_json_file(path)
        if not isinstance(payload, dict):
            continue
        questions_raw = payload.get("questions")
        questions = (
            [q for q in questions_raw if isinstance(q, dict)]
            if isinstance(questions_raw, list)
            else []
        )

        stem = path.stem
        question_ids: list[str] = []
        code_task_count = 0
        for idx, q in enumerate(questions, start=1):
            qid = str(q.get("id") or f"{stem}:{q.get('number', idx)}")
            question_ids.append(qid)
            if q.get("type") == "code_task":
                code_task_count += 1

        lecture_raw = payload.get("lecture", 0)
        lecture = (
            lecture_raw
            if isinstance(lecture_raw, int) and not isinstance(lecture_raw, bool)
            else 0
        )

        topics.append(
            {
                "id": stem,
                "title": str(payload.get("title") or stem),
                "lecture": lecture,
                "date": str(payload.get("date") or ""),
                "kind": kind,
                "question_count": len(questions),
                "question_ids": question_ids,
                "code_task_count": code_task_count,
            }
        )
    return topics


def _scan_glossary(subject_dir: Path) -> list[dict[str, str]]:
    payload = _read_json_file(subject_dir / "glosar" / "glosar.json")
    terms: list[dict[str, str]] = []
    if isinstance(payload, dict):
        banks = payload.get("banks")
        if isinstance(banks, dict):
            for bank_stem, items in banks.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get("term"), str):
                        terms.append(
                            {
                                "bank": str(bank_stem),
                                "term": item["term"].strip(),
                                "body": str(item.get("body") or "").strip(),
                            }
                        )
        elif isinstance(payload.get("terms"), list):
            for item in payload["terms"]:
                if isinstance(item, dict) and isinstance(item.get("term"), str):
                    terms.append(
                        {
                            "bank": "",
                            "term": item["term"].strip(),
                            "body": str(item.get("body") or "").strip(),
                        }
                    )
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("term"), str):
                terms.append(
                    {
                        "bank": "",
                        "term": item["term"].strip(),
                        "body": str(item.get("body") or "").strip(),
                    }
                )
    return terms


def _scan_formulas(subject_dir: Path) -> list[dict[str, str]]:
    payload = _read_json_file(subject_dir / "formulas.json")
    formulas: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return formulas
    sections = payload.get("sections")
    if not isinstance(sections, list):
        return formulas
    for section in sections:
        if not isinstance(section, dict):
            continue
        sec_title = str(section.get("title") or "").strip()
        items = section.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                formulas.append(
                    {
                        "section": sec_title,
                        "name": item["name"].strip(),
                        "latex": str(item.get("latex") or "").strip(),
                        "note": str(item.get("note") or "").strip(),
                    }
                )
    return formulas


def compute_mastery(
    question_ids: list[str],
    progress_by_qid: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compute 4-level mastery, progress_pct, readiness_pct, and binary cells."""
    total = len(question_ids)
    answered = 0
    correct_now = 0
    strong = 0
    cells: list[str] = []

    for qid in question_ids:
        row = progress_by_qid.get(qid)
        if row is None or (
            int(row.get("correct_count", 0)) == 0
            and int(row.get("wrong_count", 0)) == 0
        ):
            cells.append("·")
            continue
        answered += 1
        if int(row.get("last_correct", 0)) == 1:
            correct_now += 1
            cells.append("1")
            if (
                int(row.get("correct_count", 0)) >= 2
                or float(row.get("stability", 0.0)) >= 3.0
            ) and int(row.get("lapses", 0)) == 0:
                strong += 1
        else:
            cells.append("0")

    if total == 0 or answered == 0:
        level = "unstarted"
        progress_pct = 0
        readiness_pct = 0
    else:
        progress_pct = max(0, min(100, round(100 * correct_now / total)))
        readiness_pct = max(
            0,
            min(
                100, round(100 * (0.6 * (correct_now / total) + 0.4 * (strong / total)))
            ),
        )
        if progress_pct >= 85 and readiness_pct >= 75:
            level = "mastered"
        elif progress_pct >= 50:
            level = "proficient"
        else:
            level = "familiar"

    return {
        "answered_count": answered,
        "correct_count": correct_now,
        "progress_pct": progress_pct,
        "readiness_pct": readiness_pct,
        "mastery_level": level,
        "mastery_label": MASTERY_LABELS[level],
        "cells": cells,
    }


def _load_raw_subject_record(
    entry: dict[str, Any],
    root: Path,
) -> dict[str, Any] | None:
    slug = str(entry.get("slug") or "").strip().lower()
    if not SLUG_RE.fullmatch(slug):
        return None

    kind = str(entry.get("kind") or "local").strip().lower()
    if kind == "external":
        visibility = str(entry.get("visibility") or "public").strip().lower()
        if visibility not in ALLOWED_VISIBILITIES:
            visibility = "admin"
        ext_url = str(entry.get("url") or "").strip()
        return {
            "slug": slug,
            "kind": "external",
            "code": str(entry.get("code") or slug.upper()),
            "short": str(entry.get("short") or slug.upper()),
            "title": str(entry.get("title") or slug.upper()),
            "description": str(entry.get("description") or ""),
            "url": ext_url if ext_url.startswith("https://") else f"/predmet/{slug}",
            "external_url": ext_url if ext_url.startswith("https://") else None,
            "status": str(entry.get("status") or "active"),
            "visibility": visibility,
            "provider": entry.get("provider"),
            "engines": [],
            "sandbox": None,
            "tools": [],
            "gamification": {"xp": False, "daily_goal": False},
            "exam_config": dict(DEFAULT_EXAM_CONFIG),
            "topics_raw": [],
            "recall_topics_raw": [],
            "glossary_raw": [],
            "formulas_raw": [],
        }

    subject_dir = root / slug
    meta = _read_json_file(subject_dir / "meta.json")
    if not isinstance(meta, dict):
        return None

    visibility = str(meta.get("visibility") or "admin").strip().lower()
    if visibility not in ALLOWED_VISIBILITIES:
        visibility = "admin"

    bank_topics = _scan_bank_files(subject_dir / "banky", "bank")
    recall_topics = _scan_bank_files(
        subject_dir / "banky" / "doplnovacka", "doplnovacka"
    )
    glossary_items = _scan_glossary(subject_dir)
    formula_items = _scan_formulas(subject_dir)

    exam_raw = meta.get("exam_config")
    exam_config = dict(DEFAULT_EXAM_CONFIG)
    if isinstance(exam_raw, dict):
        for key in ("count", "minutes", "pass_pct"):
            val = exam_raw.get(key)
            if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                exam_config[key] = val

    sandbox_raw = meta.get("sandbox")
    sandbox = sandbox_raw if isinstance(sandbox_raw, dict) else None

    return {
        "slug": slug,
        "kind": "local",
        "code": str(meta.get("code") or slug.upper()),
        "short": str(meta.get("short") or slug.upper()),
        "title": str(meta.get("title") or slug.upper()),
        "description": str(meta.get("description") or ""),
        "url": f"/predmet/{slug}",
        "external_url": None,
        "status": str(
            meta.get("status") or ("active" if visibility == "public" else "draft")
        ),
        "visibility": visibility,
        "provider": meta.get("provider"),
        "engines": list(meta.get("engines") or []),
        "sandbox": sandbox,
        "tools": list(meta.get("tools") or []),
        "gamification": dict(
            meta.get("gamification") or {"xp": True, "daily_goal": True}
        ),
        "exam_config": exam_config,
        "topics_raw": bank_topics,
        "recall_topics_raw": recall_topics,
        "glossary_raw": glossary_items,
        "formulas_raw": formula_items,
    }


def _project_subject(
    raw: dict[str, Any],
    progress_by_qid: dict[str, dict[str, Any]],
    *,
    include_admin_fields: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    all_question_ids: list[str] = []
    projected_topics: list[dict[str, Any]] = []
    binary_cells: list[dict[str, str]] = []

    combined_topics = list(raw["topics_raw"]) + list(raw["recall_topics_raw"])
    for topic in combined_topics:
        qids = list(topic["question_ids"])
        all_question_ids.extend(qids)
        t_mastery = compute_mastery(qids, progress_by_qid)
        for idx, (qid, state) in enumerate(
            zip(qids, t_mastery["cells"], strict=False), start=1
        ):
            binary_cells.append(
                {
                    "id": qid,
                    "topic_id": topic["id"],
                    "state": state,
                    "label": f"{topic['title']} #{idx}",
                }
            )
        projected_topics.append(
            {
                "id": topic["id"],
                "title": topic["title"],
                "lecture": topic["lecture"],
                "date": topic["date"],
                "kind": topic["kind"],
                "question_count": topic["question_count"],
                "answered_count": t_mastery["answered_count"],
                "correct_count": t_mastery["correct_count"],
                "progress_pct": t_mastery["progress_pct"],
                "readiness_pct": t_mastery["readiness_pct"],
                "mastery_level": t_mastery["mastery_level"],
                "mastery_label": t_mastery["mastery_label"],
                "code_task_count": topic["code_task_count"],
            }
        )

    s_mastery = compute_mastery(all_question_ids, progress_by_qid)
    question_count = sum(t["question_count"] for t in raw["topics_raw"])
    recall_count = sum(t["question_count"] for t in raw["recall_topics_raw"])
    has_sandbox = bool(raw["sandbox"] and raw["sandbox"].get("languages"))

    projected: dict[str, Any] = {
        "slug": raw["slug"],
        "kind": raw["kind"],
        "code": raw["code"],
        "short": raw["short"],
        "title": raw["title"],
        "description": raw["description"],
        "url": raw["url"],
        "external_url": raw["external_url"],
        "status": raw["status"],
        "engines": raw["engines"],
        "sandbox": raw["sandbox"],
        "has_sandbox": has_sandbox,
        "code_url": f"/predmet/{raw['slug']}/code"
        if raw["kind"] == "local" and has_sandbox
        else None,
        "tools": raw["tools"],
        "gamification": raw["gamification"],
        "exam_config": raw["exam_config"],
        "question_count": question_count,
        "recall_count": recall_count,
        "total_items": question_count + recall_count,
        "glossary_count": len(raw["glossary_raw"]),
        "formula_count": len(raw["formulas_raw"]),
        "topic_count": len(projected_topics),
        "answered_count": s_mastery["answered_count"],
        "correct_count": s_mastery["correct_count"],
        "progress_pct": s_mastery["progress_pct"],
        "readiness_pct": s_mastery["readiness_pct"],
        "mastery_level": s_mastery["mastery_level"],
        "mastery_label": s_mastery["mastery_label"],
        "binary_preview": s_mastery["cells"][:BINARY_PREVIEW_LIMIT],
        "topics": projected_topics,
    }

    if include_admin_fields:
        projected["visibility"] = raw["visibility"]
        projected["provider"] = raw["provider"]

    return projected, projected_topics, binary_cells


def _group_progress_by_subject(
    user_id: int | None,
    subject_slug: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    if user_id is None:
        return {}
    rows = db.get_user_subject_progress(user_id, subject_slug=subject_slug)
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        slug = str(row["subject_slug"])
        qid = str(row["question_id"])
        grouped.setdefault(slug, {})[qid] = row
    return grouped


def load_subjects(
    *,
    user_id: int | None = None,
    is_admin: bool = False,
    include_admin_fields: bool = False,
    pinned_subjects: list[str] | None = None,
    subjects_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Load and project catalog subjects according to caller role.

    Non-admins NEVER see visibility=='admin' subjects or 'provider' metadata.
    """
    root = subjects_dir if subjects_dir is not None else SUBJECTS_DIR
    registry = load_registry(root)
    progress_map = _group_progress_by_subject(user_id)

    results: list[dict[str, Any]] = []
    for entry in registry["subjects"]:
        if not isinstance(entry, dict):
            continue
        raw = _load_raw_subject_record(entry, root)
        if raw is None:
            continue
        if not is_admin and raw["visibility"] != "public":
            continue
        projected, _, _ = _project_subject(
            raw,
            progress_map.get(raw["slug"], {}),
            include_admin_fields=(include_admin_fields and is_admin),
        )
        results.append(projected)

    if pinned_subjects:
        pin_order = {slug: idx for idx, slug in enumerate(pinned_subjects)}
        results.sort(
            key=lambda item: (
                (0, pin_order[item["slug"]]) if item["slug"] in pin_order else (1, 0)
            )
        )

    return results


def get_subject_detail(
    slug: str,
    *,
    user_id: int | None = None,
    is_admin: bool = False,
    subjects_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]] | None:
    """Return (subject, topics, binary_cells) or None if missing/unauthorized."""
    cleaned = slug.strip().lower()
    if not SLUG_RE.fullmatch(cleaned):
        return None

    root = subjects_dir if subjects_dir is not None else SUBJECTS_DIR
    registry = load_registry(root)
    for entry in registry["subjects"]:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("slug") or "").strip().lower() != cleaned:
            continue
        raw = _load_raw_subject_record(entry, root)
        if raw is None:
            return None
        if not is_admin and raw["visibility"] != "public":
            return None
        progress_map = _group_progress_by_subject(user_id, subject_slug=cleaned)
        return _project_subject(
            raw,
            progress_map.get(cleaned, {}),
            include_admin_fields=False,
        )
    return None


def search_catalog(
    query: str,
    *,
    is_admin: bool = False,
    limit: int = 25,
    subjects_dir: Path | None = None,
) -> list[dict[str, str]]:
    """Role-aware search for Ctrl+K Command Palette.

    Strictly excludes visibility=='admin' subjects and provider metadata for non-admins.
    """
    q = query.strip().lower()
    root = subjects_dir if subjects_dir is not None else SUBJECTS_DIR
    registry = load_registry(root)
    hits: list[dict[str, str]] = []

    def _matches(*fields: str) -> bool:
        if not q:
            return True
        haystack = " ".join(f for f in fields if f).lower()
        return q in haystack

    for entry in registry["subjects"]:
        if not isinstance(entry, dict):
            continue
        raw = _load_raw_subject_record(entry, root)
        if raw is None:
            continue
        if not is_admin and raw["visibility"] != "public":
            continue

        if _matches(
            raw["slug"], raw["code"], raw["short"], raw["title"], raw["description"]
        ):
            hits.append(
                {
                    "type": "subject",
                    "title": f"{raw['code']} — {raw['title']}",
                    "subtitle": raw["description"],
                    "url": raw["url"],
                    "badge": raw["short"],
                    "subject_slug": raw["slug"],
                }
            )

        if raw["kind"] != "local":
            continue

        for topic in list(raw["topics_raw"]) + list(raw["recall_topics_raw"]):
            if _matches(topic["title"], topic["id"], raw["short"]):
                hits.append(
                    {
                        "type": "topic",
                        "title": topic["title"],
                        "subtitle": f"{raw['code']} · {topic['question_count']} otázek",
                        "url": f"/predmet/{raw['slug']}?topic={topic['id']}",
                        "badge": raw["short"],
                        "subject_slug": raw["slug"],
                    }
                )

        if q:
            for item in raw["glossary_raw"]:
                if _matches(item["term"], item["body"]):
                    snippet = item["body"][:96] + (
                        "…" if len(item["body"]) > 96 else ""
                    )
                    hits.append(
                        {
                            "type": "glossary",
                            "title": item["term"],
                            "subtitle": snippet or f"Glosář {raw['short']}",
                            "url": f"/predmet/{raw['slug']}#glosar",
                            "badge": f"{raw['short']} · Glosář",
                            "subject_slug": raw["slug"],
                        }
                    )

            for formula in raw["formulas_raw"]:
                if _matches(formula["name"], formula["note"], formula["section"]):
                    hits.append(
                        {
                            "type": "formula",
                            "title": formula["name"],
                            "subtitle": formula["note"]
                            or formula["section"]
                            or f"Vzorce {raw['short']}",
                            "url": f"/predmet/{raw['slug']}#vzorce",
                            "badge": f"{raw['short']} · Vzorec",
                            "subject_slug": raw["slug"],
                        }
                    )

            for mode_name, mode_desc, suffix in _STUDY_MODES_SEARCH:
                if _matches(mode_name, mode_desc, raw["short"]):
                    hits.append(
                        {
                            "type": "mode",
                            "title": f"{raw['short']} · {mode_name}",
                            "subtitle": mode_desc,
                            "url": f"/predmet/{raw['slug']}{suffix}",
                            "badge": "Režim",
                            "subject_slug": raw["slug"],
                        }
                    )

            if raw["sandbox"] and _matches("sandbox", "code", "kód", "r", raw["short"]):
                hits.append(
                    {
                        "type": "mode",
                        "title": f"{raw['short']} · Code Sandbox",
                        "subtitle": "Interaktivní prostředí pro spouštění kódu a testů",
                        "url": f"/predmet/{raw['slug']}/code",
                        "badge": "Sandbox",
                        "subject_slug": raw["slug"],
                    }
                )

        if len(hits) >= limit:
            break

    return hits[:limit]


def set_subject_visibility(
    slug: str,
    visibility: str,
    *,
    subjects_dir: Path | None = None,
) -> dict[str, str] | None:
    """Update visibility ('public' | 'admin') for a registered subject."""
    cleaned_slug = slug.strip().lower()
    cleaned_vis = visibility.strip().lower()
    if not SLUG_RE.fullmatch(cleaned_slug):
        return None
    if cleaned_vis not in ALLOWED_VISIBILITIES:
        raise ValueError("Visibility must be 'public' or 'admin'.")

    root = subjects_dir if subjects_dir is not None else SUBJECTS_DIR
    registry_path = root / "registry.json"
    registry = _read_json_file(registry_path)
    if not isinstance(registry, dict) or not isinstance(registry.get("subjects"), list):
        return None

    for entry in registry["subjects"]:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("slug") or "").strip().lower() != cleaned_slug:
            continue

        kind = str(entry.get("kind") or "local").strip().lower()
        if kind == "external":
            entry["visibility"] = cleaned_vis
            _atomic_write_json(registry_path, registry)
            return {"slug": cleaned_slug, "visibility": cleaned_vis}

        meta_path = root / cleaned_slug / "meta.json"
        meta = _read_json_file(meta_path)
        if not isinstance(meta, dict):
            return None
        meta["visibility"] = cleaned_vis
        _atomic_write_json(meta_path, meta)
        return {"slug": cleaned_slug, "visibility": cleaned_vis}

    return None
