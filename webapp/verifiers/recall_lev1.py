"""Czech diacritic/case-normalized recall verifier with Levenshtein <= 1 parity."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_czech(text: str) -> str:
    """Fold case, strip Czech/Unicode diacritics, and normalize punctuation/spaces."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    collapsed = _NON_ALNUM_RE.sub(" ", without_marks).strip()
    return collapsed


def levenshtein_distance(a: str, b: str) -> int:
    """Compute exact Levenshtein edit distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def verify_recall_lev1(question: dict[str, Any], submitted: Any) -> dict[str, Any]:
    """Verify recall answer with diacritic folding and Levenshtein <= 1 tolerance."""
    accepted_raw: list[str] = []
    if isinstance(question.get("accept"), list):
        accepted_raw.extend(
            str(item) for item in question["accept"] if item is not None
        )
    if question.get("answer") is not None:
        ans = question["answer"]
        if isinstance(ans, list):
            accepted_raw.extend(str(item) for item in ans if item is not None)
        else:
            accepted_raw.append(str(ans))

    user_raw = "" if submitted is None else str(submitted)
    norm_user = normalize_czech(user_raw)
    explanation = str(
        question.get("explanation") or question.get("solution") or ""
    ).strip()

    best_distance: int | None = None
    matched_target: str | None = None
    matched = False

    if norm_user:
        for target in accepted_raw:
            norm_target = normalize_czech(target)
            if not norm_target:
                continue
            dist = levenshtein_distance(norm_user, norm_target)
            if best_distance is None or dist < best_distance:
                best_distance = dist
            # Allow 1 typo when target has >= 3 chars; short tokens need exact match.
            max_edits = 1 if len(norm_target) >= 3 else 0
            if dist <= max_edits:
                matched = True
                matched_target = target
                break

    display = accepted_raw[0] if accepted_raw else ""
    return {
        "correct": matched,
        "correct_display": display,
        "explanation": explanation,
        "details": {
            "submitted": user_raw.strip(),
            "normalized": norm_user,
            "distance": best_distance,
            "matched_target": matched_target,
        },
    }
