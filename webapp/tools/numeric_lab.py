"""Pure-Python numerical methods backend for the NUM interactive mini-lab."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FunctionPreset:
    id: str
    label: str
    latex: str
    default_a: float
    default_b: float
    default_x0: float
    fn: Callable[[float, float], float]
    dfn: Callable[[float, float], float]


_PRESETS: dict[str, FunctionPreset] = {
    "x2_minus_c": FunctionPreset(
        id="x2_minus_c",
        label="f(x) = x² − c",
        latex="f(x) = x^2 - c",
        default_a=0.0,
        default_b=3.0,
        default_x0=2.5,
        fn=lambda x, c: x * x - c,
        dfn=lambda x, _c: 2.0 * x,
    ),
    "x3_minus_x_minus_2": FunctionPreset(
        id="x3_minus_x_minus_2",
        label="f(x) = x³ − x − 2",
        latex="f(x) = x^3 - x - 2",
        default_a=1.0,
        default_b=2.0,
        default_x0=1.8,
        fn=lambda x, _c: x * x * x - x - 2.0,
        dfn=lambda x, _c: 3.0 * x * x - 1.0,
    ),
    "cos_minus_x": FunctionPreset(
        id="cos_minus_x",
        label="f(x) = cos(x) − x",
        latex="f(x) = \\cos(x) - x",
        default_a=0.0,
        default_b=1.5,
        default_x0=1.2,
        fn=lambda x, _c: math.cos(x) - x,
        dfn=lambda x, _c: -math.sin(x) - 1.0,
    ),
    "exp_minus_3x": FunctionPreset(
        id="exp_minus_3x",
        label="f(x) = eˣ − 3x",
        latex="f(x) = e^x - 3x",
        default_a=0.0,
        default_b=1.0,
        default_x0=0.2,
        fn=lambda x, _c: math.exp(x) - 3.0 * x,
        dfn=lambda x, _c: math.exp(x) - 3.0,
    ),
}


def _sample_curve(
    fn: Callable[[float, float], float],
    c: float,
    x_min: float,
    x_max: float,
    points: int = 80,
) -> list[dict[str, float]]:
    if x_max <= x_min:
        x_max = x_min + 2.0
    step = (x_max - x_min) / max(1, points - 1)
    out: list[dict[str, float]] = []
    for i in range(points):
        x = x_min + i * step
        try:
            y = fn(x, c)
        except (OverflowError, ValueError):
            continue
        if math.isfinite(y):
            out.append({"x": round(x, 6), "y": round(y, 6)})
    return out


def compute_bisection(
    preset_id: str = "x2_minus_c",
    *,
    a: float | None = None,
    b: float | None = None,
    c: float = 2.0,
    tol: float = 1e-6,
    max_iter: int = 12,
) -> dict[str, Any]:
    """Run bisection iterations and return convergence table + plot points."""
    preset = _PRESETS.get(preset_id) or _PRESETS["x2_minus_c"]
    left = float(preset.default_a if a is None else a)
    right = float(preset.default_b if b is None else b)
    if left > right:
        left, right = right, left
    clean_tol = max(1e-12, min(1.0, float(tol)))
    clean_iter = max(1, min(40, int(max_iter)))

    fa = preset.fn(left, c)
    fb = preset.fn(right, c)
    bracket_valid = (fa * fb) <= 0.0

    iterations: list[dict[str, float | int]] = []
    cur_a, cur_b = left, right
    cur_fa = fa
    mid = (cur_a + cur_b) / 2.0
    converged = False

    if bracket_valid:
        for k in range(1, clean_iter + 1):
            mid = (cur_a + cur_b) / 2.0
            f_mid = preset.fn(mid, c)
            err_bound = (cur_b - cur_a) / 2.0
            iterations.append(
                {
                    "k": k,
                    "a": round(cur_a, 8),
                    "b": round(cur_b, 8),
                    "mid": round(mid, 8),
                    "f_mid": round(f_mid, 8),
                    "error_bound": round(err_bound, 8),
                }
            )
            if abs(f_mid) < clean_tol or err_bound < clean_tol:
                converged = True
                break
            if cur_fa * f_mid <= 0.0:
                cur_b = mid
            else:
                cur_a = mid
                cur_fa = f_mid

    pad = max(0.3, (right - left) * 0.15)
    curve = _sample_curve(preset.fn, c, left - pad, right + pad)

    return {
        "method": "bisection",
        "preset": preset.id,
        "label": preset.label,
        "latex": preset.latex,
        "params": {
            "a": left,
            "b": right,
            "c": c,
            "tol": clean_tol,
            "max_iter": clean_iter,
        },
        "bracket_valid": bracket_valid,
        "converged": converged,
        "root": round(mid, 8) if bracket_valid else None,
        "iterations": iterations,
        "curve": curve,
    }


def compute_newton(
    preset_id: str = "x2_minus_c",
    *,
    x0: float | None = None,
    c: float = 2.0,
    tol: float = 1e-8,
    max_iter: int = 8,
) -> dict[str, Any]:
    """Run Newton-Raphson iterations and return table, tangents, and plot points."""
    preset = _PRESETS.get(preset_id) or _PRESETS["x2_minus_c"]
    cur_x = float(preset.default_x0 if x0 is None else x0)
    clean_tol = max(1e-12, min(1.0, float(tol)))
    clean_iter = max(1, min(30, int(max_iter)))

    iterations: list[dict[str, float | int]] = []
    tangents: list[dict[str, float]] = []
    visited_x = [cur_x]
    converged = False
    stopped_reason = ""

    for k in range(1, clean_iter + 1):
        fx = preset.fn(cur_x, c)
        dfx = preset.dfn(cur_x, c)
        if abs(dfx) < 1e-12:
            stopped_reason = "Derivace f'(x) je nulová — nelze dělit."
            break
        x_next = cur_x - fx / dfx
        if not math.isfinite(x_next) or abs(x_next) > 1e6:
            stopped_reason = "Iterace diverguje."
            break
        step_diff = abs(x_next - cur_x)
        iterations.append(
            {
                "k": k,
                "x": round(cur_x, 8),
                "fx": round(fx, 8),
                "dfx": round(dfx, 8),
                "x_next": round(x_next, 8),
                "step_diff": round(step_diff, 8),
            }
        )
        tangents.append(
            {
                "x0": round(cur_x, 8),
                "y0": round(fx, 8),
                "x1": round(x_next, 8),
            }
        )
        cur_x = x_next
        visited_x.append(cur_x)
        if step_diff < clean_tol or abs(preset.fn(cur_x, c)) < clean_tol:
            converged = True
            break

    x_min = min(min(visited_x), preset.default_a) - 0.5
    x_max = max(max(visited_x), preset.default_b) + 0.5
    curve = _sample_curve(preset.fn, c, x_min, x_max)

    return {
        "method": "newton",
        "preset": preset.id,
        "label": preset.label,
        "latex": preset.latex,
        "params": {
            "x0": float(preset.default_x0 if x0 is None else x0),
            "c": c,
            "tol": clean_tol,
            "max_iter": clean_iter,
        },
        "converged": converged,
        "stopped_reason": stopped_reason,
        "root": round(cur_x, 8) if iterations else None,
        "iterations": iterations,
        "tangents": tangents,
        "curve": curve,
    }


def get_numeric_lab_data(
    *,
    method: str = "bisection",
    preset: str = "x2_minus_c",
    a: float | None = None,
    b: float | None = None,
    x0: float | None = None,
    c: float = 2.0,
    tol: float = 1e-6,
    max_iter: int = 10,
) -> dict[str, Any]:
    """Unified entry point for GET /api/{slug}/tools/numeric_lab."""
    presets_meta = [
        {
            "id": p.id,
            "label": p.label,
            "latex": p.latex,
            "default_a": p.default_a,
            "default_b": p.default_b,
            "default_x0": p.default_x0,
        }
        for p in _PRESETS.values()
    ]
    if method.strip().lower() == "newton":
        result = compute_newton(preset, x0=x0, c=c, tol=tol, max_iter=max_iter)
    else:
        result = compute_bisection(preset, a=a, b=b, c=c, tol=tol, max_iter=max_iter)
    result["tool"] = "numeric_lab"
    result["presets"] = presets_meta
    return result
