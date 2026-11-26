"""Subject tools package (glossary, formulas, numeric_lab, dist_tables, automata)."""

from webapp.tools.automata import (
    PRESET_AUTOMATA,
    are_equivalent,
    get_automata_tool_data,
    parse_automaton,
    simulate_automaton,
)
from webapp.tools.dist_tables import (
    build_quantile_tables,
    chi2_cdf,
    chi2_pdf,
    chi2_quantile,
    get_dist_tables_data,
    normal_cdf,
    normal_pdf,
    normal_quantile,
    t_cdf,
    t_pdf,
    t_quantile,
)
from webapp.tools.formulas import get_formulas_tool_data
from webapp.tools.glossary import get_glossary_tool_data
from webapp.tools.numeric_lab import (
    compute_bisection,
    compute_newton,
    get_numeric_lab_data,
)
from webapp.tools.theory import get_theory_tool_data

SUPPORTED_TOOLS = frozenset(
    {"glossary", "formulas", "theory", "numeric_lab", "dist_tables", "automata"}
)

__all__ = [
    "PRESET_AUTOMATA",
    "SUPPORTED_TOOLS",
    "are_equivalent",
    "build_quantile_tables",
    "chi2_cdf",
    "chi2_pdf",
    "chi2_quantile",
    "compute_bisection",
    "compute_newton",
    "get_automata_tool_data",
    "get_dist_tables_data",
    "get_formulas_tool_data",
    "get_glossary_tool_data",
    "get_numeric_lab_data",
    "get_theory_tool_data",
    "normal_cdf",
    "normal_pdf",
    "normal_quantile",
    "parse_automaton",
    "simulate_automaton",
    "t_cdf",
    "t_pdf",
    "t_quantile",
]
