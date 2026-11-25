"""Choice (single & multi A/B/C/D) verifier."""

from __future__ import annotations

from typing import Any


def _normalize_choice_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        parts = [p.strip().upper() for p in value.replace(";", ",").split(",")]
        return {p for p in parts if p}
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip().upper() for item in value if str(item).strip()}
    return {str(value).strip().upper()}


def verify_choice(question: dict[str, Any], submitted: Any) -> dict[str, Any]:
    """Verify single-choice or multi-choice A/B/C/D answer."""
    expected_set = _normalize_choice_set(
        question.get("correct") if "correct" in question else question.get("answer")
    )
    user_set = _normalize_choice_set(submitted)

    is_correct = bool(expected_set) and user_set == expected_set

    options = (
        question.get("options") if isinstance(question.get("options"), dict) else {}
    )
    sorted_expected = sorted(expected_set)
    display_parts = []
    for key in sorted_expected:
        opt_text = options.get(key)
        display_parts.append(f"{key}: {opt_text}" if opt_text else key)

    return {
        "correct": is_correct,
        "correct_display": "; ".join(display_parts)
        if display_parts
        else ", ".join(sorted_expected),
        "explanation": str(
            question.get("explanation") or question.get("solution") or ""
        ).strip(),
        "details": {
            "expected": sorted_expected,
            "submitted": sorted(user_set),
            "multi": len(expected_set) > 1,
        },
    }
