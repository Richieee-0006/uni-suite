"""Structured formula reference sheet loader for KaTeX Tahák drawer."""

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


def get_formulas_tool_data(
    slug: str,
    *,
    query: str = "",
    subjects_dir: Path | None = None,
) -> dict[str, Any]:
    """Return structured KaTeX formula sections for a subject."""
    root = subjects_dir if subjects_dir is not None else SUBJECTS_DIR
    path = root / slug / "formulas.json"
    raw: Any = None
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = None

    q_norm = _normalize_search(query)
    sections_out: list[dict[str, Any]] = []
    flat_items: list[dict[str, str]] = []

    if isinstance(raw, dict) and isinstance(raw.get("sections"), list):
        for section in raw["sections"]:
            if not isinstance(section, dict):
                continue
            sec_title = str(section.get("title") or "").strip()
            items_in = section.get("items")
            if not isinstance(items_in, list):
                continue
            filtered_items: list[dict[str, str]] = []
            for item in items_in:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                entry = {
                    "section": sec_title,
                    "name": item["name"].strip(),
                    "latex": str(item.get("latex") or "").strip(),
                    "note": str(item.get("note") or "").strip(),
                }
                if q_norm and not (
                    q_norm in _normalize_search(entry["name"])
                    or q_norm in _normalize_search(entry["note"])
                    or q_norm in _normalize_search(sec_title)
                    or q_norm in entry["latex"].lower()
                ):
                    continue
                filtered_items.append(entry)
                flat_items.append(entry)
            if filtered_items:
                sections_out.append({"title": sec_title, "items": filtered_items})

    return {
        "tool": "formulas",
        "slug": slug,
        "query": query.strip(),
        "total": len(flat_items),
        "sections": sections_out,
        "items": flat_items,
    }
