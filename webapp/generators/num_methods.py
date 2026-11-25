"""Seeded deterministic problem generators for KI/NUM (Numerical Methods)."""

from __future__ import annotations

from typing import Any

from webapp.generators.base import ProblemGenerator


def _fmt_cz(val: float, decimals: int = 4) -> str:
    formatted = f"{val:.{decimals}f}".rstrip("0").rstrip(".")
    return formatted.replace(".", "{,}")


class NumBisectionStepGenerator(ProblemGenerator):
    """Generates bisection midpoint calculation for f(x) = x^2 - c on [a, b]."""

    generator_id = "num_bisection_step_v1"
    subject_slug = "num"
    topic_id = "02-hledani-korenu"
    title = "Bisekce — k-tá aproximace kořene"

    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rng = self.make_rng(seed)
        c = rng.choice([2, 3, 5, 6, 7, 10, 11, 13, 15])
        steps = rng.choice([2, 3])
        if isinstance(params, dict) and isinstance(params.get("steps"), int):
            steps = max(1, min(5, params["steps"]))

        # Find tight integer bracket [a0, b0] with a0^2 < c < b0^2.
        a0 = 1
        while (a0 + 1) * (a0 + 1) <= c:
            a0 += 1
        b0 = a0 + 1

        a = float(a0)
        b = float(b0)
        trace: list[str] = []
        mid = (a + b) / 2.0
        for step in range(1, steps + 1):
            mid = (a + b) / 2.0
            f_mid = mid * mid - c
            trace.append(
                f"k={step}: [{_fmt_cz(a)}, {_fmt_cz(b)}] → c_{step}={_fmt_cz(mid)}, "
                f"f(c_{step})={_fmt_cz(f_mid)}"
            )
            if (a * a - c) * f_mid < 0:
                b = mid
            else:
                a = mid

        answer = round(mid, 6)
        mid1 = _fmt_cz((a0 + b0) / 2)
        return {
            "type": "input_numeric",
            "text": (
                f"Metodou půlení intervalu (bisekce) hledáme kladný kořen rovnice "
                f"$f(x) = x^2 - {c} = 0$ na počátečním intervalu $[{a0}, {b0}]$. "
                f"Jaká je hodnota středu $c_{steps}$ v **{steps}. iteraci**?"
            ),
            "answer": answer,
            "tolerance": 1e-4,
            "tolerance_mode": "abs",
            "unit": "",
            "hints": [
                (
                    "Krok 1: V každém kroku spočítej střed "
                    "$c_k = \\frac{a_k + b_k}{2}$ a znaménko $f(c_k) = c_k^2 - c$."
                ),
                (
                    f"Krok 2: Začni z $[{a0}, {b0}]$: první střed je $c_1 = {mid1}$ "
                    "a ponech polovinu se změnou znaménka."
                ),
                "Krok 3: Postup iterací: " + " ; ".join(trace) + ".",
            ],
            "explanation": (
                f"Postup bisekce pro $f(x) = x^2 - {c}$: "
                + "; ".join(trace)
                + f". Výsledek: $c_{steps} = {_fmt_cz(answer)}$."
            ),
        }


class NumNewtonIterGenerator(ProblemGenerator):
    """Generates first Newton-Raphson iteration x_1 for f(x) = x^2 - c or x^3 - c."""

    generator_id = "num_newton_iter_v1"
    subject_slug = "num"
    topic_id = "02-hledani-korenu"
    title = "Newtonova metoda — výpočet iterace x₁"

    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rng = self.make_rng(seed)
        power = rng.choice([2, 3])
        if power == 2:
            c = rng.choice([2, 3, 5, 7, 10, 12, 15, 20])
            x0 = rng.choice([1, 2, 3, 4])
            if x0 * x0 == c:
                x0 += 1
            fx0 = x0**2 - c
            dfx0 = 2 * x0
            expr = f"x^2 - {c}"
            dexpr = "2x"
        else:
            c = rng.choice([2, 3, 5, 6, 9, 10, 12])
            x0 = rng.choice([1, 2, 3])
            if x0**3 == c:
                x0 += 1
            fx0 = x0**3 - c
            dfx0 = 3 * (x0**2)
            expr = f"x^3 - {c}"
            dexpr = "3x^2"

        x1 = x0 - fx0 / dfx0
        answer = round(x1, 6)
        ans_cz = _fmt_cz(answer)
        return {
            "type": "input_numeric",
            "text": (
                f"Použijte **Newtonovu metodu tečen** pro rovnici $f(x) = {expr} = 0$ "
                f"s počáteční aproximací $x_0 = {x0}$. Spočítejte první iteraci $x_1$ "
                "(lze zadat i jako zlomek nebo desetinné číslo na 4 místa)."
            ),
            "answer": answer,
            "tolerance": 1e-4,
            "tolerance_mode": "abs",
            "unit": "",
            "hints": [
                (
                    "Krok 1: Použij iterační vzorec "
                    "$x_1 = x_0 - \\frac{f(x_0)}{f'(x_0)}$, "
                    f"kde $f'(x) = {dexpr}$."
                ),
                (
                    f"Krok 2: Dosaď $x_0 = {x0}$: "
                    f"$f({x0}) = {fx0}$ a $f'({x0}) = {dfx0}$."
                ),
                (
                    f"Krok 3: $x_1 = {x0} - \\frac{{{fx0}}}{{{dfx0}}} "
                    f"\\approx {ans_cz}$."
                ),
            ],
            "explanation": (
                f"Pro $f(x) = {expr}$ je $f'(x) = {dexpr}$. "
                f"$x_1 = {x0} - \\frac{{{fx0}}}{{{dfx0}}} = {ans_cz}$."
            ),
        }


class NumRoundingErrorGenerator(ProblemGenerator):
    """Generates absolute or relative rounding error calculations."""

    generator_id = "num_rounding_error_v1"
    subject_slug = "num"
    topic_id = "01-chyby-a-aritmetika"
    title = "Absolutní a relativní chyba aproximace"

    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rng = self.make_rng(seed)
        kind = rng.choice(["relative", "absolute"])
        exact = rng.choice([2.5, 3.2, 4.0, 5.0, 8.0, 10.0, 12.5, 20.0, 25.0])
        delta_sign = rng.choice([-1, 1])
        delta_mag = rng.choice([0.02, 0.04, 0.05, 0.08, 0.1, 0.12, 0.15])
        approx = round(exact + delta_sign * delta_mag, 4)

        abs_err = abs(approx - exact)
        rel_err = abs_err / abs(exact)
        ex_cz = _fmt_cz(exact)
        ap_cz = _fmt_cz(approx)
        ae_cz = _fmt_cz(abs_err)

        if kind == "absolute":
            answer = round(abs_err, 6)
            ans_cz = _fmt_cz(answer)
            return {
                "type": "input_numeric",
                "text": (
                    f"Přesná hodnota veličiny je $x = {ex_cz}$ a naměřená "
                    f"aproximace je $\\tilde{{x}} = {ap_cz}$. "
                    "Určete **absolutní chybu** $\\Delta x = |\\tilde{x} - x|$."
                ),
                "answer": answer,
                "tolerance": 1e-4,
                "tolerance_mode": "abs",
                "unit": "",
                "hints": [
                    (
                        "Krok 1: Absolutní chyba je nezáporná vzdálenost "
                        "$\\Delta x = |\\tilde{x} - x|$."
                    ),
                    f"Krok 2: Odečti $|{ap_cz} - {ex_cz}|$.",
                    f"Krok 3: $\\Delta x = {ans_cz}$.",
                ],
                "explanation": (
                    f"$\\Delta x = |{ap_cz} - {ex_cz}| = {ans_cz}$."
                ),
            }

        answer = round(rel_err, 6)
        ans_cz = _fmt_cz(answer)
        return {
            "type": "input_numeric",
            "text": (
                f"Přesná hodnota je $x = {ex_cz}$ a aproximace je "
                f"$\\tilde{{x}} = {ap_cz}$. Spočítejte **relativní chybu** "
                "$\\delta x = \\frac{|\\tilde{x} - x|}{|x|}$ "
                "jako desetinné číslo (ne v procentech)."
            ),
            "answer": answer,
            "tolerance": 1e-4,
            "tolerance_mode": "abs",
            "unit": "",
            "hints": [
                (
                    "Krok 1: Nejprve urči absolutní chybu "
                    "$\\Delta x = |\\tilde{x} - x|$."
                ),
                (
                    f"Krok 2: $\\Delta x = {ae_cz}$, "
                    f"vyděl přesnou hodnotou $|x| = {ex_cz}$."
                ),
                (
                    f"Krok 3: $\\delta x = "
                    f"\\frac{{{ae_cz}}}{{{ex_cz}}} = {ans_cz}$."
                ),
            ],
            "explanation": (
                f"$\\delta x = \\frac{{|{ap_cz} - {ex_cz}|}}{{{ex_cz}}} "
                f"= \\frac{{{ae_cz}}}{{{ex_cz}}} = {ans_cz}$."
            ),
        }


class NumTrapezoidGenerator(ProblemGenerator):
    """Generates composite trapezoidal rule numerical integration problem."""

    generator_id = "num_trapezoid_v1"
    subject_slug = "num"
    topic_id = "04-interpolace-integrace-aproximace"
    title = "Složené lichoběžníkové pravidlo"

    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rng = self.make_rng(seed)
        p = rng.choice([1, 2, 3])
        q = rng.choice([0, 1, 2])
        r = rng.choice([0, 1, 2, 3])
        a = rng.choice([0, 1])
        n = rng.choice([2, 4])
        span = rng.choice([2, 4])
        b = a + span
        h = (b - a) / n

        def f(x: float) -> float:
            return p * x * x + q * x + r

        xs = [a + i * h for i in range(n + 1)]
        ys = [f(x) for x in xs]
        inner_sum = sum(ys[1:-1])
        approx = (h / 2.0) * (ys[0] + 2.0 * inner_sum + ys[-1])
        answer = round(approx, 6)

        terms = [f"{p}x^2" if p > 1 else "x^2"]
        if q > 0:
            terms.append(f"{q}x" if q > 1 else "x")
        if r > 0:
            terms.append(str(r))
        poly_latex = " + ".join(terms)
        h_cz = _fmt_cz(h)
        ans_cz = _fmt_cz(answer)
        ys_str = ", ".join(_fmt_cz(y) for y in ys)
        xs_str = ", ".join(_fmt_cz(x) for x in xs)

        return {
            "type": "input_numeric",
            "text": (
                f"Aproximujte integrál $\\int_{{{a}}}^{{{b}}} ({poly_latex})\\,dx$ "
                f"**složeným lichoběžníkovým pravidlem** s $n = {n}$ podintervaly."
            ),
            "answer": answer,
            "tolerance": 1e-3,
            "tolerance_mode": "abs",
            "unit": "",
            "hints": [
                (
                    f"Krok 1: Krok je $h = \\frac{{{b} - {a}}}{{{n}}} = {h_cz}$; "
                    "vzorec $I \\approx \\frac{h}{2}[f(x_0) + "
                    "2\\sum_{i=1}^{n-1} f(x_i) + f(x_n)]$."
                ),
                f"Krok 2: Funkční hodnoty v uzlech jsou $f(x_i) = [{ys_str}]$.",
                (
                    f"Krok 3: $I \\approx \\frac{{{h_cz}}}{{2}} \\cdot "
                    f"({_fmt_cz(ys[0])} + 2\\cdot {_fmt_cz(inner_sum)} + "
                    f"{_fmt_cz(ys[-1])}) = {ans_cz}$."
                ),
            ],
            "explanation": (
                f"Pro $h = {h_cz}$ a uzly $x_i = [{xs_str}]$ "
                f"vychází $I \\approx {ans_cz}$."
            ),
        }


NUM_GENERATORS: tuple[ProblemGenerator, ...] = (
    NumBisectionStepGenerator(),
    NumNewtonIterGenerator(),
    NumRoundingErrorGenerator(),
    NumTrapezoidGenerator(),
)
