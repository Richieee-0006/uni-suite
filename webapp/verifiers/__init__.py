"""Pluggable VerifierRegistry dispatching strictly by question type."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from webapp.tools.automata import are_equivalent, simulate_automaton
from webapp.verifiers.choice import verify_choice
from webapp.verifiers.code_sandbox import verify_code_sandbox
from webapp.verifiers.exact import verify_exact
from webapp.verifiers.numeric_eps import parse_numeric, verify_numeric_eps
from webapp.verifiers.recall_lev1 import (
    levenshtein_distance,
    normalize_czech,
    verify_recall_lev1,
)

VerifierFn = Callable[[dict[str, Any], Any], dict[str, Any]]


def _verify_composite_tool(question: dict[str, Any], submitted: Any) -> dict[str, Any]:
    """Verify embedded domain tool tasks (e.g. automata_designer)."""
    explanation = str(question.get("explanation") or "").strip()
    tool_kind = str(question.get("tool") or "automata_designer").strip().lower()

    if tool_kind == "automata_designer":
        if not isinstance(submitted, dict):
            return {
                "correct": False,
                "correct_display": "Odevzdej validní specifikaci konečného automatu.",
                "explanation": explanation,
                "details": {"error": "Invalid automaton payload."},
            }
        try:
            target = question.get("target_automaton")
            if isinstance(target, dict):
                eq = are_equivalent(submitted, target)
                return {
                    "correct": bool(eq["equivalent"]),
                    "correct_display": "Ekvivalentní automat"
                    if eq["equivalent"]
                    else f"Protipříklad: '{eq['counterexample']}'",
                    "explanation": explanation,
                    "details": eq,
                }

            accept_words = [str(w) for w in (question.get("accept_words") or [])]
            reject_words = [str(w) for w in (question.get("reject_words") or [])]
            failures: list[str] = []
            for word in accept_words:
                if not simulate_automaton(submitted, word)["accepted"]:
                    failures.append(f"měl přijmout '{word}'")
            for word in reject_words:
                if simulate_automaton(submitted, word)["accepted"]:
                    failures.append(f"měl zamítnout '{word}'")
            return {
                "correct": len(failures) == 0 and bool(accept_words or reject_words),
                "correct_display": "Všechna testovací slova prošla."
                if not failures
                else "; ".join(failures[:4]),
                "explanation": explanation,
                "details": {"failures": failures},
            }
        except ValueError as exc:
            return {
                "correct": False,
                "correct_display": str(exc),
                "explanation": explanation,
                "details": {"error": str(exc)},
            }

    return {
        "correct": False,
        "correct_display": "",
        "explanation": explanation,
        "details": {"error": f"Unsupported composite tool: {tool_kind}"},
    }


class VerifierRegistry:
    """Dispatches answer verification strictly by question['type']."""

    def __init__(self) -> None:
        self._handlers: dict[str, VerifierFn] = {}

    def register(self, question_type: str, handler: VerifierFn) -> None:
        self._handlers[question_type.strip().lower()] = handler

    def verify(self, question: dict[str, Any], submitted: Any) -> dict[str, Any]:
        qtype = str(question.get("type") or "choice").strip().lower()
        if qtype == "multi_step":
            return self._verify_multi_step(question, submitted)
        handler = self._handlers.get(qtype)
        if handler is None:
            raise ValueError(f"Unsupported question type: {qtype}")
        return handler(question, submitted)

    def _verify_multi_step(
        self, question: dict[str, Any], submitted: Any
    ) -> dict[str, Any]:
        steps = question.get("steps")
        if not isinstance(steps, list) or not steps:
            return {
                "correct": False,
                "correct_display": "",
                "explanation": str(question.get("explanation") or "").strip(),
                "details": {"steps": []},
            }

        submitted_list: list[Any]
        if isinstance(submitted, list):
            submitted_list = submitted
        elif isinstance(submitted, dict) and isinstance(submitted.get("steps"), list):
            submitted_list = submitted["steps"]
        else:
            submitted_list = []

        step_results: list[dict[str, Any]] = []
        displays: list[str] = []
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            step_ans = submitted_list[idx] if idx < len(submitted_list) else None
            res = self.verify(step, step_ans)
            step_results.append(
                {
                    "index": idx,
                    "correct": bool(res["correct"]),
                    "correct_display": res["correct_display"],
                    "details": res.get("details", {}),
                }
            )
            displays.append(f"Krok {idx + 1}: {res['correct_display']}")

        all_ok = bool(step_results) and all(r["correct"] for r in step_results)
        return {
            "correct": all_ok,
            "correct_display": " | ".join(displays),
            "explanation": str(question.get("explanation") or "").strip(),
            "details": {"steps": step_results},
        }


def build_default_verifier_registry() -> VerifierRegistry:
    registry = VerifierRegistry()
    registry.register("choice", verify_choice)
    registry.register("input_exact", verify_exact)
    registry.register("input", verify_exact)  # TZI v1 backward compatibility
    registry.register("input_numeric", verify_numeric_eps)
    registry.register("recall", verify_recall_lev1)
    registry.register("code_task", verify_code_sandbox)
    registry.register("composite_tool", _verify_composite_tool)
    return registry


default_verifier_registry = build_default_verifier_registry()

__all__ = [
    "VerifierRegistry",
    "default_verifier_registry",
    "levenshtein_distance",
    "normalize_czech",
    "parse_numeric",
    "verify_choice",
    "verify_code_sandbox",
    "verify_exact",
    "verify_numeric_eps",
    "verify_recall_lev1",
]
