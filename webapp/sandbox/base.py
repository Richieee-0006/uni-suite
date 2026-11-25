"""Base contracts, strict validation, PNG checks, concurrency & rate limits."""

from __future__ import annotations

import asyncio
import base64
import math
import struct
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PNG_MAGIC: bytes = b"\x89PNG\r\n\x1a\n"
PNG_IHDR_TAG: bytes = b"IHDR"
MAX_PNG_DIMENSION: int = 8192

MAX_CODE_BYTES: int = 16 * 1024  # 16 KB
MAX_TESTS_BYTES: int = 16 * 1024  # 16 KB combined test code
MAX_TESTS_COUNT: int = 32
MAX_STDOUT_BYTES: int = 32 * 1024  # 32 KB
MAX_STDERR_BYTES: int = 16 * 1024  # 16 KB
MAX_PLOTS: int = 8
MAX_PLOT_BYTES: int = 512 * 1024  # 512 KB
MIN_TIMEOUT_SEC: float = 1.0
MAX_TIMEOUT_SEC: float = 10.0
DEFAULT_TIMEOUT_SEC: float = 5.0
MAX_CONCURRENT_RUNS: int = 2
RATE_LIMIT_MAX_REQUESTS: int = 10
RATE_LIMIT_WINDOW_SEC: float = 60.0

ALLOWED_THEMES: frozenset[str] = frozenset({"dark", "light"})
TRUNCATION_SUFFIX: bytes = b"\n...[zkraceno na limit 32 KB]"

# Global Tier 2 container concurrency guard (max 2 simultaneous executions).
EXECUTION_SEMAPHORE: asyncio.Semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)


class SandboxError(Exception):
    """Base exception for sandbox failures."""


class ValidationError(SandboxError):
    """Raised when an execution request or artifact violates security limits."""


class RateLimitError(SandboxError):
    """Raised when a user exceeds the per-minute sandbox execution quota."""

    def __init__(self, message: str, retry_after: float = 60.0) -> None:
        super().__init__(message)
        self.retry_after = max(1.0, float(retry_after))


class DriverNotFoundError(SandboxError):
    """Raised when no sandbox driver is registered for the requested language."""


@dataclass(frozen=True)
class SandboxLimits:
    """Declarative summary of security & resource limits for sandbox runs."""

    max_code_bytes: int = MAX_CODE_BYTES
    max_stdout_bytes: int = MAX_STDOUT_BYTES
    max_stderr_bytes: int = MAX_STDERR_BYTES
    max_plots: int = MAX_PLOTS
    max_plot_bytes: int = MAX_PLOT_BYTES
    min_timeout_sec: float = MIN_TIMEOUT_SEC
    max_timeout_sec: float = MAX_TIMEOUT_SEC
    max_concurrent_runs: int = MAX_CONCURRENT_RUNS
    rate_limit_per_minute: int = RATE_LIMIT_MAX_REQUESTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_code_bytes": self.max_code_bytes,
            "max_stdout_bytes": self.max_stdout_bytes,
            "max_stderr_bytes": self.max_stderr_bytes,
            "max_plots": self.max_plots,
            "max_plot_bytes": self.max_plot_bytes,
            "min_timeout_sec": self.min_timeout_sec,
            "max_timeout_sec": self.max_timeout_sec,
            "max_concurrent_runs": self.max_concurrent_runs,
            "rate_limit_per_minute": self.rate_limit_per_minute,
        }


def truncate_utf8(
    text: str,
    max_bytes: int = MAX_STDOUT_BYTES,
    suffix: bytes = TRUNCATION_SUFFIX,
) -> str:
    """Truncate a string to at most ``max_bytes`` UTF-8 bytes without splitting."""
    if not isinstance(text, str):
        text = str(text)
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    if max_bytes <= 0:
        return ""
    if len(suffix) >= max_bytes:
        return raw[:max_bytes].decode("utf-8", errors="ignore")
    budget = max_bytes - len(suffix)
    cut = raw[:budget]
    # Back off past any incomplete multi-byte UTF-8 trailing sequence.
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    if cut:
        # Also drop a leading byte if the full codepoint did not fit.
        try:
            prefix_text = cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
            prefix_text = cut.decode("utf-8", errors="ignore")
    else:
        prefix_text = ""
    return prefix_text + suffix.decode("utf-8", errors="ignore")


def validate_png_bytes(
    data: bytes,
    max_bytes: int = MAX_PLOT_BYTES,
) -> tuple[int, int]:
    """Validate raw raster PNG bytes and return ``(width, height)``.

    Strictly enforces PNG magic bytes, IHDR chunk presence, bounded dimensions,
    and maximum byte length to block forged SVG/HTML or oversized payloads.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ValidationError("Plot artifact must be raw PNG bytes.")
    raw = bytes(data)
    if len(raw) < 24:
        raise ValidationError("Plot artifact is too small to be a valid PNG.")
    if len(raw) > max_bytes:
        raise ValidationError(
            f"Plot artifact exceeds maximum size ({max_bytes} B)."
        )
    if not raw.startswith(PNG_MAGIC):
        raise ValidationError(
            "Invalid plot format: missing PNG magic bytes."
        )
    ihdr_len = struct.unpack(">I", raw[8:12])[0]
    if ihdr_len != 13 or raw[12:16] != PNG_IHDR_TAG:
        raise ValidationError("Invalid PNG structure: missing valid IHDR chunk.")
    width, height = struct.unpack(">II", raw[16:24])
    if (
        width < 1
        or height < 1
        or width > MAX_PNG_DIMENSION
        or height > MAX_PNG_DIMENSION
    ):
        raise ValidationError("Invalid PNG dimensions.")
    return width, height


@dataclass
class PlotArtifact:
    """Validated raster PNG plot produced by a sandbox execution."""

    data: bytes
    caption: str = ""
    width: int = 0
    height: int = 0

    def __post_init__(self) -> None:
        width, height = validate_png_bytes(self.data)
        self.data = bytes(self.data)
        self.width = width
        self.height = height
        if len(self.caption) > 200:
            self.caption = self.caption[:200]

    def to_base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass
class TestCase:
    """Single automated test case evaluated against student code."""

    __test__ = False
    name: str
    code: str
    expected: Any = None
    tolerance: float = 1e-4
    hidden: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError("Test case name must be a non-empty string.")
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValidationError("Test case code must be a non-empty string.")
        if len(self.name) > 160:
            raise ValidationError("Test case name is too long.")
        try:
            tol = float(self.tolerance)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Invalid numeric tolerance.") from exc
        if not math.isfinite(tol) or tol < 0:
            raise ValidationError("Tolerance must be a finite non-negative number.")
        self.tolerance = tol
        self.hidden = bool(self.hidden)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "code": self.code,
            "expected": self.expected,
            "tolerance": self.tolerance,
            "hidden": self.hidden,
        }


@dataclass
class TestResult:
    """Outcome of one test case."""

    __test__ = False
    name: str
    passed: bool
    actual: Any = None
    expected: Any = None
    message: str = ""
    elapsed_ms: int = 0
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "passed": bool(self.passed),
            "elapsed_ms": max(0, int(self.elapsed_ms)),
            "hidden": bool(self.hidden),
            "message": truncate_utf8(str(self.message), 512),
        }
        if self.hidden:
            out["actual"] = "ověřeno" if self.passed else "neshoda"
            out["expected"] = None
        else:
            out["actual"] = self.actual
            out["expected"] = self.expected
        return out


@dataclass
class TableDiff:
    """Expected vs. Actual tabular output comparison."""

    columns: list[str]
    expected_rows: list[list[Any]]
    actual_rows: list[list[Any]]
    row_matches: list[bool]
    matched: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "expected_rows": [list(r) for r in self.expected_rows],
            "actual_rows": [list(r) for r in self.actual_rows],
            "row_matches": [bool(m) for m in self.row_matches],
            "matched": bool(self.matched),
        }


def _cells_equal(a: Any, b: Any, tolerance: float = 1e-4) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tolerance)
    return str(a) == str(b)


def compute_table_diff(
    columns: list[str],
    expected_rows: list[list[Any]],
    actual_rows: list[list[Any]],
    tolerance: float = 1e-4,
) -> TableDiff:
    """Compute row-by-row match status for expected vs. actual tabular rows."""
    row_matches: list[bool] = []
    max_len = max(len(expected_rows), len(actual_rows))
    for idx in range(max_len):
        if idx >= len(expected_rows) or idx >= len(actual_rows):
            row_matches.append(False)
            continue
        exp_row = expected_rows[idx]
        act_row = actual_rows[idx]
        if len(exp_row) != len(act_row):
            row_matches.append(False)
            continue
        same = all(
            _cells_equal(a, e, tolerance=tolerance)
            for a, e in zip(act_row, exp_row, strict=True)
        )
        row_matches.append(same)
    matched = (
        len(expected_rows) == len(actual_rows)
        and all(row_matches)
    )
    return TableDiff(
        columns=[str(c) for c in columns],
        expected_rows=[list(r) for r in expected_rows],
        actual_rows=[list(r) for r in actual_rows],
        row_matches=row_matches,
        matched=matched,
    )


@dataclass
class ExecutionRequest:
    """Validated input payload for any LanguageDriver."""

    language: str
    code: str
    tests: list[TestCase] = field(default_factory=list)
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    theme: str = "dark"
    dataset: Any = None
    expected_table: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.language, str) or not self.language.strip():
            raise ValidationError("Language identifier must be non-empty.")
        self.language = self.language.strip().lower()

        if not isinstance(self.code, str):
            raise ValidationError("Code payload must be a string.")
        code_bytes = len(self.code.encode("utf-8"))
        if code_bytes == 0:
            raise ValidationError("Code payload must not be empty.")
        if code_bytes > MAX_CODE_BYTES:
            raise ValidationError(
                f"Code exceeds maximum allowed size of {MAX_CODE_BYTES} bytes."
            )

        try:
            timeout_val = float(self.timeout_sec)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Timeout must be a numeric value.") from exc
        if (
            not math.isfinite(timeout_val)
            or timeout_val < MIN_TIMEOUT_SEC
            or timeout_val > MAX_TIMEOUT_SEC
        ):
            raise ValidationError(
                f"Timeout must be between {MIN_TIMEOUT_SEC:g}s "
                f"and {MAX_TIMEOUT_SEC:g}s."
            )
        self.timeout_sec = timeout_val

        theme_clean = str(self.theme or "dark").strip().lower()
        if theme_clean not in ALLOWED_THEMES:
            raise ValidationError("Theme must be 'dark' or 'light'.")
        self.theme = theme_clean

        if len(self.tests) > MAX_TESTS_COUNT:
            raise ValidationError(
                f"Too many tests (max {MAX_TESTS_COUNT})."
            )
        normalized_tests: list[TestCase] = []
        tests_bytes = 0
        for item in self.tests:
            tc = item if isinstance(item, TestCase) else TestCase(**item)
            tests_bytes += len(tc.code.encode("utf-8"))
            if tests_bytes > MAX_TESTS_BYTES:
                raise ValidationError(
                    f"Combined test code exceeds {MAX_TESTS_BYTES} bytes."
                )
            normalized_tests.append(tc)
        self.tests = normalized_tests


@dataclass
class ExecutionResult:
    """Normalized, size-bounded result returned by any LanguageDriver."""

    ok: bool
    status: str
    stdout: str = ""
    stderr: str = ""
    tests: list[TestResult] = field(default_factory=list)
    table_diff: TableDiff | None = None
    plots: list[PlotArtifact] = field(default_factory=list)
    elapsed_ms: int = 0
    driver: str = ""

    def __post_init__(self) -> None:
        self.enforce_limits()

    def enforce_limits(self) -> None:
        self.stdout = truncate_utf8(self.stdout, MAX_STDOUT_BYTES)
        self.stderr = truncate_utf8(self.stderr, MAX_STDERR_BYTES)
        validated_plots: list[PlotArtifact] = []
        for plot in self.plots[:MAX_PLOTS]:
            if isinstance(plot, PlotArtifact):
                validated_plots.append(plot)
            else:
                validated_plots.append(PlotArtifact(data=plot))
        self.plots = validated_plots
        self.elapsed_ms = max(0, int(self.elapsed_ms))

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "tests": [t.to_dict() for t in self.tests],
            "table_diff": (
                self.table_diff.to_dict() if self.table_diff is not None else None
            ),
            "plots_b64": [p.to_base64() for p in self.plots],
            "elapsed_ms": self.elapsed_ms,
        }


class SlidingWindowRateLimiter:
    """Per-user sliding-window rate limiter (default: 10 requests / 60 s)."""

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window_seconds: float = RATE_LIMIT_WINDOW_SEC,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(1.0, float(window_seconds))
        self._clock = clock or time.monotonic
        self._buckets: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = deque()
            self._buckets[key] = bucket
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def check(self, key: str) -> tuple[bool, float]:
        """Record a request if under quota; returns ``(allowed, retry_after)``."""
        now = self._clock()
        bucket = self._prune(key, now)
        if len(bucket) >= self.max_requests:
            retry_after = max(0.1, (bucket[0] + self.window_seconds) - now)
            return False, retry_after
        bucket.append(now)
        # Opportunistic cleanup to keep bucket dictionary bounded.
        if len(self._buckets) > 2048:
            cutoff = now - self.window_seconds
            stale = [
                k for k, dq in self._buckets.items()
                if not dq or dq[-1] <= cutoff
            ]
            for stale_key in stale:
                self._buckets.pop(stale_key, None)
        return True, 0.0

    def check_or_raise(self, key: str) -> None:
        allowed, retry_after = self.check(key)
        if not allowed:
            raise RateLimitError(
                "Překročen limit spuštění sandboxu (10 za minutu).",
                retry_after=retry_after,
            )

    def remaining(self, key: str) -> int:
        now = self._clock()
        bucket = self._prune(key, now)
        return max(0, self.max_requests - len(bucket))

    def reset(self) -> None:
        self._buckets.clear()


class LanguageDriver(ABC):
    """Abstract interface implemented by every Tier 2 execution driver."""

    driver_id: str = ""
    language: str = ""
    display_name: str = ""
    tier: str = "tier2_docker"
    max_timeout_sec: float = MAX_TIMEOUT_SEC

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """Return ``(available, reason)`` without mutating host state."""

    @abstractmethod
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute validated request inside the driver's isolation boundary."""

    async def run_guarded(
        self,
        request: ExecutionRequest,
        semaphore: asyncio.Semaphore | None = None,
    ) -> ExecutionResult:
        """Validate, guard with global concurrency semaphore, and bound output."""
        request.validate()
        if request.timeout_sec > self.max_timeout_sec:
            raise ValidationError(
                f"Driver '{self.driver_id}' max timeout is "
                f"{self.max_timeout_sec:g}s."
            )
        available, reason = self.is_available()
        if not available:
            return ExecutionResult(
                ok=False,
                status="unavailable",
                stderr=reason,
                elapsed_ms=0,
                driver=self.driver_id,
            )

        sem = semaphore if semaphore is not None else EXECUTION_SEMAPHORE
        started = time.monotonic()
        async with sem:
            result = await self.execute(request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if result.elapsed_ms <= 0:
            result.elapsed_ms = elapsed_ms
        if not result.driver:
            result.driver = self.driver_id
        result.enforce_limits()
        return result

    def describe(self) -> dict[str, Any]:
        available, reason = self.is_available()
        return {
            "id": self.driver_id,
            "language": self.language,
            "name": self.display_name,
            "tier": self.tier,
            "available": available,
            "reason": "" if available else reason,
            "max_timeout_sec": self.max_timeout_sec,
        }
