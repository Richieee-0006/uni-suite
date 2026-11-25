"""Code task verifier validating Tier-1 WASM or Tier-2 sandbox test summaries."""

from __future__ import annotations

from typing import Any


def _extract_expected_test_names(
    question: dict[str, Any],
) -> tuple[list[str], list[str]]:
    visible = [
        str(t.get("name") or f"visible_{idx}").strip()
        for idx, t in enumerate(question.get("visible_tests") or [], start=1)
        if isinstance(t, dict)
    ]
    hidden = [
        str(t.get("name") or f"hidden_{idx}").strip()
        for idx, t in enumerate(question.get("hidden_tests") or [], start=1)
        if isinstance(t, dict)
    ]
    return visible, hidden


def verify_code_sandbox(question: dict[str, Any], submitted: Any) -> dict[str, Any]:
    """Verify code_task submission against Tier-1 or Tier-2 test summary."""
    explanation = str(question.get("explanation") or "").strip()
    visible_names, hidden_names = _extract_expected_test_names(question)
    required_visible = set(visible_names)
    expected_lang = str(question.get("language") or "r").strip().lower()

    if not isinstance(submitted, dict):
        return {
            "correct": False,
            "correct_display": (
                f"Všechny testy ({len(visible_names)}) musí projít (PASS)."
            ),
            "explanation": explanation,
            "details": {"error": "Chybí výsledek spuštění testů ze sandboxu."},
        }

    reported_ok = bool(submitted.get("ok"))
    tests_raw = submitted.get("tests")
    tests = (
        [t for t in tests_raw if isinstance(t, dict)]
        if isinstance(tests_raw, list)
        else []
    )
    driver = str(submitted.get("driver") or "webr").strip()
    tier = str(submitted.get("tier") or "tier1_wasm").strip()
    language = str(submitted.get("language") or expected_lang).strip().lower()

    if language != expected_lang:
        return {
            "correct": False,
            "correct_display": f"Očekáván jazyk {expected_lang}.",
            "explanation": explanation,
            "details": {
                "error": "Language mismatch",
                "expected_language": expected_lang,
            },
        }

    passed_names = {
        str(t.get("name") or "").strip()
        for t in tests
        if t.get("passed") is True and str(t.get("name") or "").strip()
    }
    failed_tests = [
        str(t.get("name") or "test") for t in tests if t.get("passed") is not True
    ]

    if required_visible:
        missing_visible = sorted(required_visible - passed_names)
        all_visible_passed = len(missing_visible) == 0
    else:
        missing_visible = []
        all_visible_passed = len(tests) > 0 and len(failed_tests) == 0

    hidden_checked = False
    missing_hidden: list[str] = []
    if tier == "tier2_docker" and hidden_names:
        hidden_checked = True
        missing_hidden = sorted(set(hidden_names) - passed_names)

    is_correct = (
        reported_ok
        and all_visible_passed
        and len(failed_tests) == 0
        and (not hidden_checked or len(missing_hidden) == 0)
    )

    passed_count = sum(1 for t in tests if t.get("passed") is True)
    total_count = max(len(tests), len(visible_names))
    return {
        "correct": is_correct,
        "correct_display": f"{passed_count}/{total_count} testů PASS ({driver})",
        "explanation": explanation,
        "details": {
            "driver": driver,
            "tier": tier,
            "passed_count": passed_count,
            "total_count": total_count,
            "failed_tests": failed_tests,
            "missing_visible": missing_visible,
            "missing_hidden": missing_hidden,
        },
    }
