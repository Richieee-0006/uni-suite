"""Searchable glossary tool backend for subject reference drawer and tools API."""

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


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _extract_entries(payload: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if isinstance(payload, dict):
        banks = payload.get("banks")
        if isinstance(banks, dict):
            for bank_id, items in banks.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get("term"), str):
                        entries.append(
                            {
                                "bank": str(bank_id),
                                "term": item["term"].strip(),
                                "body": str(
                                    item.get("body") or item.get("definition") or ""
                                ).strip(),
                                "category": str(
                                    item.get("category") or bank_id
                                ).strip(),
                            }
                        )
        elif isinstance(payload.get("terms"), list):
            for item in payload["terms"]:
                if isinstance(item, dict) and isinstance(item.get("term"), str):
                    entries.append(
                        {
                            "bank": str(item.get("bank") or ""),
                            "term": item["term"].strip(),
                            "body": str(
                                item.get("body") or item.get("definition") or ""
                            ).strip(),
                            "category": str(item.get("category") or "").strip(),
                        }
                    )
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("term"), str):
                entries.append(
                    {
                        "bank": str(item.get("bank") or ""),
                        "term": item["term"].strip(),
                        "body": str(
                            item.get("body") or item.get("definition") or ""
                        ).strip(),
                        "category": str(item.get("category") or "").strip(),
                    }
                )
    return entries


def get_glossary_tool_data(
    slug: str,
    *,
    query: str = "",
    bank: str = "",
    subjects_dir: Path | None = None,
) -> dict[str, Any]:
    """Return searchable glossary terms for a subject."""
    root = subjects_dir if subjects_dir is not None else SUBJECTS_DIR
    glosar_dir = root / slug / "glosar"
    entries = _extract_entries(_read_json(glosar_dir / "glosar.json"))
    extras = _extract_entries(_read_json(glosar_dir / "doplnky.json"))

    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, str]] = []
    for item in (*entries, *extras):
        key = (item["bank"], item["term"].lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    bank_filter = bank.strip()
    if bank_filter:
        merged = [m for m in merged if m["bank"] == bank_filter]

    q_norm = _normalize_search(query)
    if q_norm:
        merged = [
            m
            for m in merged
            if q_norm in _normalize_search(m["term"])
            or q_norm in _normalize_search(m["body"])
            or q_norm in _normalize_search(m["category"])
        ]

    banks_grouped: dict[str, list[dict[str, str]]] = {}
    for item in merged:
        group = item["bank"] or "obecne"
        banks_grouped.setdefault(group, []).append(item)

    return {
        "tool": "glossary",
        "slug": slug,
        "query": query.strip(),
        "total": len(merged),
        "items": merged,
        "banks": banks_grouped,
    }
