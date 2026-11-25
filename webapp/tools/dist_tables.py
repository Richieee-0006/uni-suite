"""Exact pure-Python statistical distributions (Normal, Student-t, Chi-square).

Zero external scientific dependencies (uses math.erf, math.lgamma, Lentz continued
fractions for regularized incomplete beta/gamma, and Newton/bisection quantiles).
"""

from __future__ import annotations

import math
from typing import Any

_SQRT_2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)
_EPS = 1e-14
_FPMIN = 1e-300


# ---------------------------------------------------------------------------
# Normal distribution N(mu, sigma^2)
# ---------------------------------------------------------------------------


def normal_pdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Probability density function of N(mu, sigma^2)."""
    if sigma <= 0.0:
        raise ValueError("sigma must be > 0")
    z = (x - mu) / sigma
    return math.exp(-0.5 * z * z) / (sigma * _SQRT_2PI)


def normal_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Cumulative distribution function of N(mu, sigma^2) via math.erf."""
    if sigma <= 0.0:
        raise ValueError("sigma must be > 0")
    z = (x - mu) / (sigma * _SQRT_2)
    return 0.5 * (1.0 + math.erf(z))


def _std_normal_quantile_acklam(p: float) -> float:
    """Acklam's rational approximation to Phi^{-1}(p), refined by Halley/Newton."""
    if not (0.0 < p < 1.0):
        raise ValueError("p must lie strictly in (0, 1)")

    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )

    # Two Newton-Raphson refinement steps using exact math.erf.
    for _ in range(2):
        err = normal_cdf(x) - p
        pdf = normal_pdf(x)
        if pdf > 0.0:
            x -= err / pdf
    return x


def normal_quantile(p: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Quantile (inverse CDF) of N(mu, sigma^2)."""
    if sigma <= 0.0:
        raise ValueError("sigma must be > 0")
    return mu + sigma * _std_normal_quantile_acklam(p)


# ---------------------------------------------------------------------------
# Regularized incomplete beta I_x(a, b) for exact Student-t CDF
# ---------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """Modified Lentz continued fraction for incomplete beta."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


# ---------------------------------------------------------------------------
# Student-t distribution
# ---------------------------------------------------------------------------


def t_pdf(x: float, df: float) -> float:
    """Probability density function of Student-t with df > 0 degrees of freedom."""
    if df <= 0.0:
        raise ValueError("df must be > 0")
    coeff = math.exp(math.lgamma((df + 1.0) / 2.0) - math.lgamma(df / 2.0)) / math.sqrt(
        df * math.pi
    )
    return coeff * ((1.0 + (x * x) / df) ** (-(df + 1.0) / 2.0))


def t_cdf(x: float, df: float) -> float:
    """Exact cumulative distribution function of Student-t via incomplete beta."""
    if df <= 0.0:
        raise ValueError("df must be > 0")
    if x == 0.0:
        return 0.5
    u = df / (df + x * x)
    tail = 0.5 * _regularized_incomplete_beta(u, df / 2.0, 0.5)
    return (1.0 - tail) if x > 0.0 else tail


def t_quantile(p: float, df: float) -> float:
    """Quantile (inverse CDF) of Student-t via bracketed bisection + Newton."""
    if df <= 0.0:
        raise ValueError("df must be > 0")
    if not (0.0 < p < 1.0):
        raise ValueError("p must lie strictly in (0, 1)")
    if abs(p - 0.5) < 1e-15:
        return 0.0
    if p < 0.5:
        return -t_quantile(1.0 - p, df)

    if abs(df - 1.0) < 1e-12:
        return math.tan(math.pi * (p - 0.5))

    # Bracket right-tail quantile.
    low = 0.0
    high = max(2.0, normal_quantile(p) * 1.5)
    while t_cdf(high, df) < p:
        high *= 2.0
        if high > 1e9:
            break

    for _ in range(80):
        mid = 0.5 * (low + high)
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
        if (high - low) < 1e-11 * max(1.0, abs(mid)):
            break

    x = 0.5 * (low + high)
    for _ in range(3):
        pdf = t_pdf(x, df)
        if pdf > 1e-18:
            x -= (t_cdf(x, df) - p) / pdf
    return x


# ---------------------------------------------------------------------------
# Regularized incomplete gamma P(a, x) for exact Chi-square CDF
# ---------------------------------------------------------------------------


def _gamma_p_series(a: float, x: float) -> float:
    gln = math.lgamma(a)
    ap = a
    term = 1.0 / a
    total = term
    for _ in range(400):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - gln)


def _gamma_q_cf(a: float, x: float) -> float:
    gln = math.lgamma(a)
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 400):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def _regularized_lower_gamma(a: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        return max(0.0, min(1.0, _gamma_p_series(a, x)))
    return max(0.0, min(1.0, 1.0 - _gamma_q_cf(a, x)))


# ---------------------------------------------------------------------------
# Chi-square distribution
# ---------------------------------------------------------------------------


def chi2_pdf(x: float, df: float) -> float:
    """Probability density function of Chi-square with df > 0 degrees of freedom."""
    if df <= 0.0:
        raise ValueError("df must be > 0")
    if x < 0.0:
        return 0.0
    if x == 0.0:
        if abs(df - 2.0) < 1e-12:
            return 0.5
        return 0.0 if df > 2.0 else float("inf")
    k2 = df / 2.0
    return math.exp(
        (k2 - 1.0) * math.log(x) - x / 2.0 - k2 * math.log(2.0) - math.lgamma(k2)
    )


def chi2_cdf(x: float, df: float) -> float:
    """Exact cumulative distribution function of Chi-square via incomplete gamma."""
    if df <= 0.0:
        raise ValueError("df must be > 0")
    if x <= 0.0:
        return 0.0
    return _regularized_lower_gamma(df / 2.0, x / 2.0)


def chi2_quantile(p: float, df: float) -> float:
    """Quantile (inverse CDF) of Chi-square via Wilson-Hilferty + bisection + Newton."""
    if df <= 0.0:
        raise ValueError("df must be > 0")
    if not (0.0 < p < 1.0):
        raise ValueError("p must lie strictly in (0, 1)")

    # Wilson-Hilferty approximation as starting upper bound estimate.
    z = _std_normal_quantile_acklam(p)
    wh = df * max(0.05, (1.0 - 2.0 / (9.0 * df) + z * math.sqrt(2.0 / (9.0 * df)))) ** 3

    low = 0.0
    high = max(2.0, wh * 1.5, df + 10.0)
    while chi2_cdf(high, df) < p:
        high *= 2.0
        if high > 1e9:
            break

    for _ in range(90):
        mid = 0.5 * (low + high)
        if chi2_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
        if (high - low) < 1e-11 * max(1.0, mid):
            break

    x = 0.5 * (low + high)
    for _ in range(3):
        pdf = chi2_pdf(x, df)
        if math.isfinite(pdf) and pdf > 1e-18:
            candidate = x - (chi2_cdf(x, df) - p) / pdf
            if candidate > 0.0:
                x = candidate
    return x


# ---------------------------------------------------------------------------
# Tool API payload builder (curves, critical regions, reference quantile tables)
# ---------------------------------------------------------------------------


def build_quantile_tables() -> dict[str, Any]:
    """Generate standard Czech exam quantile tables for N(0,1), t_df, and chi2_df."""
    norm_probs = [0.50, 0.75, 0.80, 0.90, 0.95, 0.975, 0.99, 0.995, 0.999]
    norm_table = [{"p": p, "q": round(normal_quantile(p), 4)} for p in norm_probs]

    t_probs = [0.90, 0.95, 0.975, 0.99, 0.995]
    t_dfs = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 60, 120]
    t_table = [
        {
            "df": df,
            "values": {str(p): round(t_quantile(p, float(df)), 4) for p in t_probs},
        }
        for df in t_dfs
    ]

    chi2_probs = [0.025, 0.05, 0.90, 0.95, 0.975, 0.99]
    chi2_dfs = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20]
    chi2_table = [
        {
            "df": df,
            "values": {
                str(p): round(chi2_quantile(p, float(df)), 4) for p in chi2_probs
            },
        }
        for df in chi2_dfs
    ]

    return {
        "normal": norm_table,
        "t_probs": t_probs,
        "t": t_table,
        "chi2_probs": chi2_probs,
        "chi2": chi2_table,
    }


def get_dist_tables_data(
    *,
    dist: str = "normal",
    mu: float = 0.0,
    sigma: float = 1.0,
    df: int = 10,
    alpha: float = 0.05,
    tail: str = "two",
    x_value: float | None = None,
    include_tables: bool = True,
) -> dict[str, Any]:
    """Return distribution curve, critical values, p-value, and quantile tables."""
    clean_dist = dist.strip().lower()
    if clean_dist not in {"normal", "t", "chi2"}:
        clean_dist = "normal"

    clean_sigma = max(0.1, min(1000.0, float(sigma)))
    clean_mu = max(-1e4, min(1e4, float(mu)))
    clean_df = max(1, min(200, int(df)))
    clean_alpha = max(0.001, min(0.5, float(alpha)))
    clean_tail = tail.strip().lower()
    if clean_tail not in {"two", "left", "right"}:
        clean_tail = "two"

    points = 120
    curve: list[dict[str, float]] = []
    critical: dict[str, float | None] = {"left": None, "right": None}
    p_value: float | None = None
    cdf_at_x: float | None = None

    if clean_dist == "normal":
        x_min = clean_mu - 4.0 * clean_sigma
        x_max = clean_mu + 4.0 * clean_sigma
        step = (x_max - x_min) / (points - 1)
        for i in range(points):
            xv = x_min + i * step
            curve.append(
                {
                    "x": round(xv, 5),
                    "y": round(normal_pdf(xv, clean_mu, clean_sigma), 6),
                }
            )
        if clean_tail == "two":
            critical["left"] = round(
                normal_quantile(clean_alpha / 2.0, clean_mu, clean_sigma), 5
            )
            critical["right"] = round(
                normal_quantile(1.0 - clean_alpha / 2.0, clean_mu, clean_sigma), 5
            )
        elif clean_tail == "left":
            critical["left"] = round(
                normal_quantile(clean_alpha, clean_mu, clean_sigma), 5
            )
        else:
            critical["right"] = round(
                normal_quantile(1.0 - clean_alpha, clean_mu, clean_sigma), 5
            )

        if x_value is not None:
            cdf_at_x = round(normal_cdf(x_value, clean_mu, clean_sigma), 6)
            if clean_tail == "left":
                p_value = cdf_at_x
            elif clean_tail == "right":
                p_value = round(1.0 - cdf_at_x, 6)
            else:
                p_value = round(2.0 * min(cdf_at_x, 1.0 - cdf_at_x), 6)

    elif clean_dist == "t":
        span = 5.0 if clean_df >= 4 else 8.0
        x_min, x_max = -span, span
        step = (x_max - x_min) / (points - 1)
        for i in range(points):
            xv = x_min + i * step
            curve.append({"x": round(xv, 5), "y": round(t_pdf(xv, float(clean_df)), 6)})
        if clean_tail == "two":
            q = t_quantile(1.0 - clean_alpha / 2.0, float(clean_df))
            critical["left"] = round(-q, 5)
            critical["right"] = round(q, 5)
        elif clean_tail == "left":
            critical["left"] = round(t_quantile(clean_alpha, float(clean_df)), 5)
        else:
            critical["right"] = round(t_quantile(1.0 - clean_alpha, float(clean_df)), 5)

        if x_value is not None:
            cdf_at_x = round(t_cdf(x_value, float(clean_df)), 6)
            if clean_tail == "left":
                p_value = cdf_at_x
            elif clean_tail == "right":
                p_value = round(1.0 - cdf_at_x, 6)
            else:
                p_value = round(2.0 * min(cdf_at_x, 1.0 - cdf_at_x), 6)

    else:
        x_min = 0.05 if clean_df <= 2 else 0.0
        x_max = max(12.0, chi2_quantile(0.998, float(clean_df)))
        step = (x_max - x_min) / (points - 1)
        for i in range(points):
            xv = x_min + i * step
            yv = chi2_pdf(xv, float(clean_df))
            if math.isfinite(yv):
                curve.append({"x": round(xv, 5), "y": round(yv, 6)})
        if clean_tail == "two":
            critical["left"] = round(
                chi2_quantile(clean_alpha / 2.0, float(clean_df)), 5
            )
            critical["right"] = round(
                chi2_quantile(1.0 - clean_alpha / 2.0, float(clean_df)), 5
            )
        elif clean_tail == "left":
            critical["left"] = round(chi2_quantile(clean_alpha, float(clean_df)), 5)
        else:
            critical["right"] = round(
                chi2_quantile(1.0 - clean_alpha, float(clean_df)), 5
            )

        if x_value is not None:
            cdf_at_x = round(chi2_cdf(x_value, float(clean_df)), 6)
            if clean_tail == "left":
                p_value = cdf_at_x
            elif clean_tail == "right":
                p_value = round(1.0 - cdf_at_x, 6)
            else:
                p_value = round(2.0 * min(cdf_at_x, 1.0 - cdf_at_x), 6)

    payload: dict[str, Any] = {
        "tool": "dist_tables",
        "dist": clean_dist,
        "params": {
            "mu": clean_mu,
            "sigma": clean_sigma,
            "df": clean_df,
            "alpha": clean_alpha,
            "tail": clean_tail,
            "x_value": x_value,
        },
        "critical": critical,
        "cdf": cdf_at_x,
        "p_value": p_value,
        "curve": curve,
    }
    if include_tables:
        payload["tables"] = build_quantile_tables()
    return payload
