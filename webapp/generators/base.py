"""Base abstractions and registry for seeded deterministic problem generators."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, TypeVar

T = TypeVar("T")


class SeededRNG:
    """Cross-platform deterministic PRNG backed by SHA-256 counter mode."""

    def __init__(self, seed: int, salt: str = "") -> None:
        self._seed = int(seed)
        self._salt = str(salt)
        self._counter = 0

    def next_u64(self) -> int:
        payload = f"{self._salt}:{self._seed}:{self._counter}".encode("utf-8")
        self._counter += 1
        digest = hashlib.sha256(payload).digest()
        return int.from_bytes(digest[:8], "big")

    def random(self) -> float:
        """Return a deterministic float in [0.0, 1.0)."""
        return (self.next_u64() >> 11) / float(1 << 53)

    def randint(self, low: int, high: int) -> int:
        """Return a deterministic integer N such that low <= N <= high."""
        if high < low:
            raise ValueError("high must be >= low")
        span = high - low + 1
        return low + (self.next_u64() % span)

    def choice(self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("Cannot choose from an empty sequence.")
        return items[self.next_u64() % len(items)]


class ProblemGenerator(ABC):
    """Contract for a deterministic parameterized problem generator."""

    generator_id: str
    subject_slug: str
    topic_id: str
    title: str

    def make_rng(self, seed: int) -> SeededRNG:
        return SeededRNG(seed=int(seed), salt=self.generator_id)

    @abstractmethod
    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a complete question dict reconstructible solely from (id, seed)."""


class GeneratorRegistry:
    """Registry mapping generator_id -> ProblemGenerator without subject branching."""

    def __init__(self) -> None:
        self._generators: dict[str, ProblemGenerator] = {}

    def register(self, generator: ProblemGenerator) -> None:
        self._generators[generator.generator_id] = generator

    def get(self, generator_id: str) -> ProblemGenerator | None:
        return self._generators.get(generator_id)

    def list_for_subject(self, subject_slug: str) -> list[dict[str, str]]:
        slug = subject_slug.strip().lower()
        return [
            {
                "id": gen.generator_id,
                "subject_slug": gen.subject_slug,
                "topic_id": gen.topic_id,
                "title": gen.title,
            }
            for gen in self._generators.values()
            if gen.subject_slug == slug
        ]

    def generate(
        self,
        generator_id: str,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        gen = self.get(generator_id)
        if gen is None:
            raise KeyError(f"Unknown generator: {generator_id}")
        clean_seed = int(seed)
        question = gen.generate(clean_seed, params=params)
        question["id"] = f"gen:{generator_id}:{clean_seed}"
        question["topic_id"] = question.get("topic_id") or gen.topic_id
        question["generator"] = {
            "id": generator_id,
            "seed": clean_seed,
            "params": dict(params) if isinstance(params, dict) else {},
        }
        return question


def parse_generated_qid(question_id: str) -> tuple[str, int] | None:
    """Parse synthetic generated question IDs of form 'gen:<generator_id>:<seed>'."""
    if not isinstance(question_id, str) or not question_id.startswith("gen:"):
        return None
    parts = question_id.split(":")
    if len(parts) != 3:
        return None
    gen_id = parts[1].strip()
    try:
        seed = int(parts[2])
    except ValueError:
        return None
    if not gen_id:
        return None
    return gen_id, seed
