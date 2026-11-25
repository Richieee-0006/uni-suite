"""Tolerance-aware numeric verifier ('.', ',', fractions, scientific notation)."""

from __future__ import annotations

import math
import re
from typing import Any

_NUMERIC_SCALAR_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def parse_numeric(value: Any) -> float | None:
    """Parse decimals ('.' or ','), fractions ('17/12'), and scientific ('1.5e-4')."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        return num if math.isfinite(num) else None

    raw = str(value).strip()
    if not raw:
        return None

    # Normalize unicode minus/spaces and Czech decimal comma.
    cleaned = (
        raw.replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u00a0", " ")
        .replace(",", ".")
        .strip()
    )
    # Remove spaces around fraction slash or thousands grouping spaces between digits.
    cleaned = re.sub(r"\s*/\s*", "/", cleaned)
    cleaned = re.sub(r"(?<=\d)\s+(?=\d)", "", cleaned)

    if "/" in cleaned:
        parts = cleaned.split("/")
        if len(parts) != 2:
            return None
        num_str, den_str = parts[0].strip(), parts[1].strip()
        if not (
            _NUMERIC_SCALAR_RE.fullmatch(num_str)
            and _NUMERIC_SCALAR_RE.fullmatch(den_str)
        ):
            return None
        try:
            numerator = float(num_str)
            denominator = float(den_str)
        except ValueError:
            return None
        if (
            not (math.isfinite(numerator) and math.isfinite(denominator))
            or denominator == 0.0
        ):
            return None
        result = numerator / denominator
        return result if math.isfinite(result) else None

    if not _NUMERIC_SCALAR_RE.fullmatch(cleaned):
        return None
    try:
        result = float(cleaned)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def verify_numeric_eps(question: dict[str, Any], submitted: Any) -> dict[str, Any]:
    """Verify numeric answer against abs/rel tolerance epsilon."""
    expected = parse_numeric(question.get("answer"))
    user_val = parse_numeric(submitted)
    tol = float(question.get("tolerance", 1e-3))
    if not math.isfinite(tol) or tol < 0.0:
        tol = 1e-3
    mode = str(question.get("tolerance_mode") or "abs").strip().lower()
    if mode not in {"abs", "rel", "both"}:
        mode = "abs"

    unit = str(question.get("unit") or "").strip()
    explanation = str(
        question.get("explanation") or question.get("solution") or ""
    ).strip()

    if expected is None:
        return {
            "correct": False,
            "correct_display": "",
            "explanation": explanation,
            "details": {"error": "Invalid expected answer in question bank."},
        }

    expected_display = f"{expected:g}" + (f" {unit}" if unit else "")

    if user_val is None:
        return {
            "correct": False,
            "correct_display": expected_display,
            "explanation": explanation,
            "details": {
                "parse_error": True,
                "expected": expected,
                "tolerance": tol,
                "tolerance_mode": mode,
            },
        }

    diff = abs(user_val - expected)
    abs_ok = diff <= tol + 1e-12
    if abs(expected) > 0.0:
        rel_err = diff / abs(expected)
        rel_ok = rel_err <= tol + 1e-12
    else:
        rel_err = diff
        rel_ok = abs_ok

    if mode == "rel":
        is_correct = rel_ok
    elif mode == "both":
        is_correct = abs_ok or rel_ok
    else:
        is_correct = abs_ok

    return {
        "correct": is_correct,
        "correct_display": expected_display,
        "explanation": explanation,
        "details": {
            "parsed_value": user_val,
            "expected": expected,
            "diff": diff,
            "rel_error": rel_err,
            "tolerance": tol,
            "tolerance_mode": mode,
        },
    }
