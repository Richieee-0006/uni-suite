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


def _clean_comparison(raw_table: Any) -> dict[str, Any] | None:
    if not isinstance(raw_table, dict):
        return None
    headers = [
        str(h).strip()
        for h in (raw_table.get("headers") or [])
        if isinstance(h, str) and h.strip()
    ]
    rows_out: list[list[str]] = []
    for row in raw_table.get("rows") or []:
        if isinstance(row, list) and row:
            cells = [str(c).strip() for c in row]
            if any(cells):
                rows_out.append(cells)
    if not headers or not rows_out:
        return None
    return {
        "caption": str(raw_table.get("caption") or "").strip(),
        "headers": headers,
        "rows": rows_out,
    }


def _clean_example(raw_ex: Any) -> dict[str, Any] | None:
    if not isinstance(raw_ex, dict):
        return None
    title = str(raw_ex.get("title") or "").strip()
    steps = [
        str(s).strip()
        for s in (raw_ex.get("steps") or [])
        if isinstance(s, str) and s.strip()
    ]
    if not steps:
        return None
    return {"title": title, "steps": steps}


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
            tags = [
                str(t).strip()
                for t in (chapter.get("tags") or [])
                if isinstance(t, str) and t.strip()
            ]
            concepts = _clean_concept_list(chapter.get("concepts"))
            formulas = _clean_formula_list(chapter.get("formulas"))
            comparison = _clean_comparison(chapter.get("comparison"))
            example = _clean_example(chapter.get("example"))
            pitfalls = [
                str(p).strip()
                for p in (chapter.get("pitfalls") or [])
                if isinstance(p, str) and p.strip()
            ]
            code_tip = str(chapter.get("code_tip") or "").strip()

            if q_norm:
                haystack_parts = [title, weeks, summary, code_tip, *tags, *pitfalls]
                for c in concepts:
                    haystack_parts.extend((c["title"], c["body"]))
                for f in formulas:
                    haystack_parts.extend((f["name"], f["note"], f["latex"]))
                if comparison:
                    haystack_parts.append(comparison["caption"])
                    haystack_parts.extend(comparison["headers"])
                    for r in comparison["rows"]:
                        haystack_parts.extend(r)
                if example:
                    haystack_parts.append(example["title"])
                    haystack_parts.extend(example["steps"])
                if q_norm not in _normalize_search(" ".join(haystack_parts)):
                    continue

            chapters_out.append(
                {
                    "id": ch_id,
                    "weeks": weeks,
                    "title": title,
                    "topic_id": topic_id,
                    "tags": tags,
                    "summary": summary,
                    "concepts": concepts,
                    "formulas": formulas,
                    "comparison": comparison,
                    "example": example,
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
