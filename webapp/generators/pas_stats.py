"""Seeded deterministic problem generators for KI/PAS (Probability & Statistics)."""

from __future__ import annotations

import math
from typing import Any

from webapp.generators.base import ProblemGenerator
from webapp.tools.dist_tables import normal_cdf, normal_quantile


def _fmt_cz(val: float, decimals: int = 4) -> str:
    formatted = f"{val:.{decimals}f}".rstrip("0").rstrip(".")
    return formatted.replace(".", "{,}")


class PasZScoreProbGenerator(ProblemGenerator):
    """Generates normal standardization (z-score) or CDF probability questions."""

    generator_id = "pas_zscore_prob_v1"
    subject_slug = "pas"
    topic_id = "02-rozdeleni-pravdepodobnosti"
    title = "Standardizace a pravděpodobnost u N(μ, σ²)"

    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rng = self.make_rng(seed)
        mu = rng.choice([50, 70, 100, 120, 175, 180])
        sigma = rng.choice([4, 5, 8, 10, 12, 15])
        z_target = rng.choice([-2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0])
        x_val = mu + z_target * sigma
        ask_prob = rng.choice([False, True])
        x_cz = _fmt_cz(x_val)
        z_cz = _fmt_cz(z_target)

        if not ask_prob:
            answer = round(z_target, 4)
            ans_cz = _fmt_cz(answer)
            return {
                "type": "input_numeric",
                "text": (
                    f"Veličina má normální rozdělení $X \\sim N({mu}, {sigma}^2)$ "
                    f"(tedy $\\mu = {mu},\\; \\sigma = {sigma}$). "
                    f"Spočítejte standardizované **$z$-skóre** pro $x = {x_cz}$."
                ),
                "answer": answer,
                "tolerance": 1e-3,
                "tolerance_mode": "abs",
                "unit": "",
                "hints": [
                    (
                        "Krok 1: Standardizace převádí $X$ na $N(0, 1)$ "
                        "vzorcem $z = \\frac{x - \\mu}{\\sigma}$."
                    ),
                    f"Krok 2: Dosaď $z = \\frac{{{x_cz} - {mu}}}{{{sigma}}}$.",
                    f"Krok 3: $z = {ans_cz}$.",
                ],
                "explanation": (
                    f"$z = \\frac{{{x_cz} - {mu}}}{{{sigma}}} = {ans_cz}$."
                ),
            }

        prob = normal_cdf(x_val, mu=float(mu), sigma=float(sigma))
        answer = round(prob, 4)
        ans_cz = _fmt_cz(answer)
        return {
            "type": "input_numeric",
            "text": (
                f"Pro $X \\sim N({mu}, {sigma}^2)$ "
                f"($\\mu = {mu},\\; \\sigma = {sigma}$) "
                f"určete pravděpodobnost $P(X \\le {x_cz})$ "
                "(na 3 desetinná místa; lze použít tabulku $N(0,1)$)."
            ),
            "answer": answer,
            "tolerance": 2e-3,
            "tolerance_mode": "abs",
            "unit": "",
            "hints": [
                (
                    "Krok 1: Nejprve standardizuj: "
                    "$P(X \\le x) = \\Phi\\left(\\frac{x - \\mu}{\\sigma}\\right)$."
                ),
                f"Krok 2: $z = \\frac{{{x_cz} - {mu}}}{{{sigma}}} = {z_cz}$.",
                (
                    "Krok 3: Z tabulky normovaného normálního rozdělení "
                    f"$\\Phi({z_cz}) \\approx {ans_cz}$."
                ),
            ],
            "explanation": (
                f"Standardizací dostaneme $z = {z_cz}$ a tedy "
                f"$P(X \\le {x_cz}) = \\Phi({z_cz}) \\approx {ans_cz}$."
            ),
        }


class PasSampleMeanVarGenerator(ProblemGenerator):
    """Generates sample mean or unbiased sample variance s^2 from a small dataset."""

    generator_id = "pas_sample_mean_var_v1"
    subject_slug = "pas"
    topic_id = "03-odhady-a-intervaly-spolehlivosti"
    title = "Výběrový průměr a výběrový rozptyl s²"

    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rng = self.make_rng(seed)
        center = rng.randint(6, 20)
        offsets = rng.choice(
            [
                [-2, -1, 0, 1, 2],
                [-4, -2, 0, 2, 4],
                [-3, -1, 1, 3],
                [-2, 0, 0, 2],
                [-3, -1, 0, 1, 3],
                [-4, -1, 0, 2, 3],
            ]
        )
        sample = [center + d for d in offsets]
        n = len(sample)
        total = sum(sample)
        mean = total / n
        sq_sum = sum((v - mean) ** 2 for v in sample)
        sample_var = sq_sum / (n - 1)
        ask_var = rng.choice([True, False])
        sample_str = ", ".join(str(v) for v in sample)
        mean_cz = _fmt_cz(mean)
        sq_cz = _fmt_cz(sq_sum)

        if not ask_var:
            answer = round(mean, 4)
            ans_cz = _fmt_cz(answer)
            return {
                "type": "input_numeric",
                "text": (
                    f"Máme náhodný výběr $n = {n}$ hodnot: $\\{{{sample_str}\\}}$. "
                    "Spočítejte **výběrový průměr** $\\bar{x}$."
                ),
                "answer": answer,
                "tolerance": 1e-3,
                "tolerance_mode": "abs",
                "unit": "",
                "hints": [
                    (
                        "Krok 1: Výběrový průměr je "
                        "$\\bar{x} = \\frac{1}{n}\\sum_{i=1}^n x_i$."
                    ),
                    f"Krok 2: Součet hodnot je ${total}$ a rozsah je $n = {n}$.",
                    f"Krok 3: $\\bar{{x}} = \\frac{{{total}}}{{{n}}} = {ans_cz}$.",
                ],
                "explanation": (
                    f"$\\bar{{x}} = \\frac{{{total}}}{{{n}}} = {ans_cz}$."
                ),
            }

        answer = round(sample_var, 4)
        ans_cz = _fmt_cz(answer)
        return {
            "type": "input_numeric",
            "text": (
                f"Máme náhodný výběr $n = {n}$ hodnot: $\\{{{sample_str}\\}}$. "
                "Spočítejte nevychýlený **výběrový rozptyl** "
                "$s^2 = \\frac{1}{n - 1}\\sum_{i=1}^n (x_i - \\bar{x})^2$."
            ),
            "answer": answer,
            "tolerance": 1e-3,
            "tolerance_mode": "abs",
            "unit": "",
            "hints": [
                (
                    "Krok 1: Nejprve urči průměr $\\bar{x}$ a poté sečti "
                    "čtverce odchylek $(x_i - \\bar{x})^2$."
                ),
                (
                    f"Krok 2: $\\bar{{x}} = {mean_cz}$, součet čtverců je "
                    f"${sq_cz}$, dělíme $n - 1 = {n - 1}$."
                ),
                f"Krok 3: $s^2 = \\frac{{{sq_cz}}}{{{n - 1}}} = {ans_cz}$.",
            ],
            "explanation": (
                f"$\\bar{{x}} = {mean_cz}$, $\\sum (x_i - \\bar{{x}})^2 = {sq_cz}$, "
                f"tedy $s^2 = \\frac{{{sq_cz}}}{{{n - 1}}} = {ans_cz}$."
            ),
        }


class PasCIHalfWidthGenerator(ProblemGenerator):
    """Generates confidence interval half-width (margin of error) for known sigma."""

    generator_id = "pas_ci_halfwidth_v1"
    subject_slug = "pas"
    topic_id = "03-odhady-a-intervaly-spolehlivosti"
    title = "Polovina šířky intervalu spolehlivosti (známé σ)"

    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rng = self.make_rng(seed)
        sigma = rng.choice([6, 8, 10, 12, 15, 18, 20])
        n = rng.choice([16, 25, 36, 49, 64, 100])
        conf_pct, alpha = rng.choice([(90, 0.10), (95, 0.05), (99, 0.01)])
        z_crit = normal_quantile(1.0 - alpha / 2.0)
        se = sigma / math.sqrt(n)
        half_width = z_crit * se
        answer = round(half_width, 4)
        z_cz3 = _fmt_cz(z_crit, 3)
        z_cz4 = _fmt_cz(z_crit, 4)
        se_cz = _fmt_cz(se, 4)
        ans_cz3 = _fmt_cz(answer, 3)
        ans_cz4 = _fmt_cz(answer, 4)

        return {
            "type": "input_numeric",
            "text": (
                f"U výběru $n = {n}$ z normálního rozdělení se známou směrodatnou "
                f"odchylkou $\\sigma = {sigma}$ sestavujeme **${conf_pct}\\,\\%$ "
                "oboustranný interval spolehlivosti** pro $\\mu$. Určete polovinu "
                "šířky $E = u_{1-\\alpha/2} \\cdot \\frac{\\sigma}{\\sqrt{n}}$."
            ),
            "answer": answer,
            "tolerance": 2e-2,
            "tolerance_mode": "abs",
            "unit": "",
            "hints": [
                (
                    f"Krok 1: Pro ${conf_pct}\\,\\%$ spolehlivost najdi kvantil "
                    f"$u_{{1-\\alpha/2}} \\approx {z_cz3}$."
                ),
                (
                    "Krok 2: Směrodatná chyba průměru je "
                    f"$\\text{{SE}} = \\frac{{{sigma}}}{{\\sqrt{{{n}}}}} = {se_cz}$."
                ),
                f"Krok 3: $E = {z_cz3} \\cdot {se_cz} \\approx {ans_cz3}$.",
            ],
            "explanation": (
                f"$\\text{{SE}} = \\frac{{{sigma}}}{{\\sqrt{{{n}}}}} = {se_cz}$, "
                f"$u_{{1-\\alpha/2}} \\approx {z_cz4}$, "
                f"tedy $E \\approx {ans_cz4}$."
            ),
        }


class PasOneSampleTStatGenerator(ProblemGenerator):
    """Generates one-sample t-test statistic calculation."""

    generator_id = "pas_t_stat_v1"
    subject_slug = "pas"
    topic_id = "04-testovani-hypotez-a-regrese"
    title = "Jednovýběrová t-statistika"

    def generate(
        self,
        seed: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rng = self.make_rng(seed)
        mu0 = rng.choice([50, 80, 100, 120, 200])
        n = rng.choice([9, 16, 25, 36])
        s = rng.choice([3, 4, 5, 6, 8, 10])
        diff = rng.choice([-4.0, -3.0, -2.0, -1.5, 1.5, 2.0, 3.0, 4.0])
        x_bar = mu0 + diff
        se = s / math.sqrt(n)
        t_val = (x_bar - mu0) / se
        answer = round(t_val, 4)
        xb_cz = _fmt_cz(x_bar)
        se_cz = _fmt_cz(se)
        diff_cz = _fmt_cz(diff)
        ans_cz = _fmt_cz(answer)

        return {
            "type": "input_numeric",
            "text": (
                f"Testujeme hypotézu $H_0: \\mu = {mu0}$ na základě výběru "
                f"$n = {n}$ s průměrem $\\bar{{x}} = {xb_cz}$ a směrodatnou "
                f"odchylkou $s = {s}$. Spočítejte testovou statistiku "
                "$t = \\frac{\\bar{x} - \\mu_0}{s / \\sqrt{n}}$."
            ),
            "answer": answer,
            "tolerance": 1e-3,
            "tolerance_mode": "abs",
            "unit": "",
            "hints": [
                (
                    "Krok 1: Nejprve vypočti směrodatnou chybu "
                    "$\\text{SE} = \\frac{s}{\\sqrt{n}}$."
                ),
                (
                    f"Krok 2: $\\text{{SE}} = \\frac{{{s}}}{{\\sqrt{{{n}}}}} "
                    f"= {se_cz}$, rozdíl $\\bar{{x}} - \\mu_0 = {diff_cz}$."
                ),
                f"Krok 3: $t = \\frac{{{diff_cz}}}{{{se_cz}}} = {ans_cz}$.",
            ],
            "explanation": (
                f"$\\text{{SE}} = \\frac{{{s}}}{{\\sqrt{{{n}}}}} = {se_cz}$, "
                f"tedy $t = \\frac{{{xb_cz} - {mu0}}}{{{se_cz}}} = {ans_cz}$."
            ),
        }


PAS_GENERATORS: tuple[ProblemGenerator, ...] = (
    PasZScoreProbGenerator(),
    PasSampleMeanVarGenerator(),
    PasCIHalfWidthGenerator(),
    PasOneSampleTStatGenerator(),
)
