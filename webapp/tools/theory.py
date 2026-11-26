"""Structured subject theory chapters loader for /predmet/{slug}/teorie & tools API."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from webapp.config import SUBJECTS_DIR


def _normalize_search(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.split())


def _clean_formula_list(raw_items: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw_items, list):
        return out
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        latex = str(item.get("latex") or "").strip()
        note = str(item.get("note") or "").strip()
        if name or latex:
            out.append({"name": name, "latex": latex, "note": note})
    return out


def _clean_concept_list(raw_items: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw_items, list):
        return out
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        if title or body:
            out.append({"title": title, "body": body})
    return out


def get_theory_tool_data(
    slug: str,
    *,
    query: str = "",
    subjects_dir: Path | None = None,
) -> dict[str, Any]:
    """Return structured theory chapters for a subject."""
    root = subjects_dir if subjects_dir is not None else SUBJECTS_DIR
    path = root / slug / "theory.json"
    raw: Any = None
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = None

    intro = str(raw.get("intro") or "").strip() if isinstance(raw, dict) else ""
    q_norm = _normalize_search(query)
    chapters_out: list[dict[str, Any]] = []

    if isinstance(raw, dict) and isinstance(raw.get("chapters"), list):
        for idx, chapter in enumerate(raw["chapters"], start=1):
            if not isinstance(chapter, dict):
                continue
            title = str(chapter.get("title") or "").strip()
            if not title:
                continue
            ch_id = str(chapter.get("id") or f"kapitola-{idx:02d}").strip()
            weeks = str(chapter.get("weeks") or "").strip()
            topic_id = str(chapter.get("topic_id") or "").strip()
            summary = str(chapter.get("summary") or "").strip()
            concepts = _clean_concept_list(chapter.get("concepts"))
            formulas = _clean_formula_list(chapter.get("formulas"))
            pitfalls = [
                str(p).strip()
                for p in (chapter.get("pitfalls") or [])
                if isinstance(p, str) and p.strip()
            ]
            code_tip = str(chapter.get("code_tip") or "").strip()

            if q_norm:
                haystack_parts = [title, weeks, summary, code_tip, *pitfalls]
                for c in concepts:
                    haystack_parts.extend((c["title"], c["body"]))
                for f in formulas:
                    haystack_parts.extend((f["name"], f["note"], f["latex"]))
                if q_norm not in _normalize_search(" ".join(haystack_parts)):
                    continue

            chapters_out.append(
                {
                    "id": ch_id,
                    "weeks": weeks,
                    "title": title,
                    "topic_id": topic_id,
                    "summary": summary,
                    "concepts": concepts,
                    "formulas": formulas,
                    "pitfalls": pitfalls,
                    "code_tip": code_tip,
                }
            )

    return {
        "tool": "theory",
        "slug": slug,
        "query": query.strip(),
        "intro": intro,
        "total": len(chapters_out),
        "chapters": chapters_out,
    }
