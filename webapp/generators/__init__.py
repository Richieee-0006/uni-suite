"""Deterministic problem generator registry for UNI Engine v2."""

from __future__ import annotations

from typing import Any

from webapp.generators.base import (
    GeneratorRegistry,
    ProblemGenerator,
    SeededRNG,
    parse_generated_qid,
)
from webapp.generators.num_methods import NUM_GENERATORS
from webapp.generators.pas_stats import PAS_GENERATORS


def build_default_generator_registry() -> GeneratorRegistry:
    registry = GeneratorRegistry()
    for gen in (*NUM_GENERATORS, *PAS_GENERATORS):
        registry.register(gen)
    return registry


default_generator_registry = build_default_generator_registry()


def generate_question(
    generator_id: str,
    seed: int,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a deterministic question by (generator_id, seed)."""
    return default_generator_registry.generate(generator_id, seed, params=params)


def reconstruct_generated_question(
    question_id: str,
    *,
    generator_id: str | None = None,
    seed: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Reconstruct a generated question from explicit (generator_id, seed) or qid."""
    resolved_id = generator_id
    resolved_seed = seed
    if resolved_id is None or resolved_seed is None:
        parsed = parse_generated_qid(question_id)
        if parsed is None:
            return None
        resolved_id, resolved_seed = parsed
    if default_generator_registry.get(resolved_id) is None:
        return None
    return default_generator_registry.generate(
        resolved_id,
        int(resolved_seed),
        params=params,
    )


def list_generators_for_subject(subject_slug: str) -> list[dict[str, str]]:
    """Return metadata for all registered generators belonging to subject_slug."""
    return default_generator_registry.list_for_subject(subject_slug)


__all__ = [
    "GeneratorRegistry",
    "ProblemGenerator",
    "SeededRNG",
    "default_generator_registry",
    "generate_question",
    "list_generators_for_subject",
    "parse_generated_qid",
    "reconstruct_generated_question",
]
