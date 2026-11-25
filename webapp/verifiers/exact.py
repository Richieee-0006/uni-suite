"""Exact string / base-conversion / TZI v1 reveal verifier."""

from __future__ import annotations

import re
from typing import Any

_BASE_PREFIX_RE = re.compile(r"^(?:0b|0x|0o)", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _normalize_exact(
    text: str, *, case_sensitive: bool = False, compact: bool = False
) -> str:
    cleaned = _WS_RE.sub(" ", text.strip())
    if not case_sensitive:
        cleaned = cleaned.lower()
    if compact:
        cleaned = cleaned.replace(" ", "").replace("_", "")
    return cleaned


def verify_exact(question: dict[str, Any], submitted: Any) -> dict[str, Any]:
    """Verify normalized exact text / base-conversion answers and TZI reveal tasks."""
    explanation = str(
        question.get("explanation") or question.get("solution") or ""
    ).strip()

    # TZI v1 self-assessed proof/reveal cards have reveal=True without answer string.
    if (
        question.get("reveal") is True
        and not question.get("answer")
        and not question.get("accept")
    ):
        if isinstance(submitted, dict):
            is_correct = bool(submitted.get("self_correct"))
        elif isinstance(submitted, bool):
            is_correct = submitted
        else:
            token = str(submitted or "").strip().lower()
            is_correct = token in {"1", "true", "ok", "yes", "__reveal_ok__"}
        return {
            "correct": is_correct,
            "correct_display": explanation or "Ukázkové řešení",
            "explanation": explanation,
            "details": {"reveal": True},
        }

    case_sensitive = bool(question.get("case_sensitive", False))

    accepted_raw: list[str] = []
    if isinstance(question.get("accept"), list):
        accepted_raw.extend(str(a) for a in question["accept"] if a is not None)
    if question.get("answer") is not None:
        ans = question["answer"]
        if isinstance(ans, list):
            accepted_raw.extend(str(a) for a in ans if a is not None)
        else:
            accepted_raw.append(str(ans))

    user_str = "" if submitted is None else str(submitted)
    norm_user = _normalize_exact(user_str, case_sensitive=case_sensitive)
    compact_user = _normalize_exact(
        user_str, case_sensitive=case_sensitive, compact=True
    )
    stripped_prefix_user = _BASE_PREFIX_RE.sub("", compact_user)

    matched = False
    for target in accepted_raw:
        norm_target = _normalize_exact(target, case_sensitive=case_sensitive)
        if norm_user == norm_target:
            matched = True
            break
        # Support grouped digits/spaces or 0b/0x prefixes for base conversion answers.
        if " " not in norm_target and norm_target:
            compact_target = _normalize_exact(
                target, case_sensitive=case_sensitive, compact=True
            )
            if compact_user == compact_target:
                matched = True
                break
            if (
                not _BASE_PREFIX_RE.match(compact_target)
                and stripped_prefix_user == compact_target
            ):
                matched = True
                break

    display = accepted_raw[0] if accepted_raw else ""
    return {
        "correct": matched,
        "correct_display": display,
        "explanation": explanation,
        "details": {
            "submitted": user_str.strip(),
            "normalized": norm_user,
        },
    }
