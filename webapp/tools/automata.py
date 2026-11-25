"""Finite Automaton (DFA/NFA with epsilon) simulator & equivalence tester."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

MAX_STATES = 64
MAX_SUBSET_STATES = 256
MAX_WORD_LEN = 256
_EPSILON_TOKENS = frozenset({"", "ε", "eps", "epsilon", "λ"})


@dataclass(frozen=True)
class Automaton:
    """Normalized finite automaton 5-tuple M = (Q, Sigma, delta, q0, F)."""

    states: tuple[str, ...]
    alphabet: tuple[str, ...]
    start: str
    accept: frozenset[str]
    transitions: dict[tuple[str, str], frozenset[str]]


def _is_epsilon(symbol: str) -> bool:
    return symbol in _EPSILON_TOKENS


def parse_automaton(spec: dict[str, Any]) -> Automaton:
    """Validate and normalize a JSON automaton specification into an Automaton."""
    if not isinstance(spec, dict):
        raise ValueError("Specifikace automatu musí být objekt.")

    raw_states = spec.get("states")
    if not isinstance(raw_states, list) or not raw_states:
        raise ValueError("Automat musí obsahovat alespoň jeden stav (states).")
    states = tuple(dict.fromkeys(str(s).strip() for s in raw_states if str(s).strip()))
    if not states or len(states) > MAX_STATES:
        raise ValueError(f"Počet stavů musí být 1 až {MAX_STATES}.")
    state_set = set(states)

    start = str(spec.get("start") or spec.get("q0") or states[0]).strip()
    if start not in state_set:
        raise ValueError(f"Počáteční stav '{start}' není v množině stavů.")

    raw_accept = spec.get("accept") if "accept" in spec else spec.get("final", [])
    if not isinstance(raw_accept, list):
        raise ValueError("Přijímající stavy (accept) musí být seznam.")
    accept = frozenset(
        str(s).strip() for s in raw_accept if str(s).strip() in state_set
    )

    raw_alphabet = spec.get("alphabet") or spec.get("sigma") or []
    if not isinstance(raw_alphabet, list):
        raise ValueError("Abeceda (alphabet) musí být seznam znaků.")
    alphabet_list: list[str] = []
    for sym in raw_alphabet:
        s = str(sym)
        if not _is_epsilon(s) and s not in alphabet_list:
            alphabet_list.append(s)

    trans_map: dict[tuple[str, str], set[str]] = {}
    raw_transitions = spec.get("transitions") or spec.get("delta") or {}

    if isinstance(raw_transitions, dict):
        for src, edges in raw_transitions.items():
            src_s = str(src).strip()
            if src_s not in state_set or not isinstance(edges, dict):
                continue
            for sym, dst in edges.items():
                sym_s = "" if _is_epsilon(str(sym)) else str(sym)
                if sym_s and sym_s not in alphabet_list:
                    alphabet_list.append(sym_s)
                targets = dst if isinstance(dst, list) else [dst]
                for target in targets:
                    t_s = str(target).strip()
                    if t_s in state_set:
                        trans_map.setdefault((src_s, sym_s), set()).add(t_s)
    elif isinstance(raw_transitions, list):
        for edge in raw_transitions:
            if not isinstance(edge, dict):
                continue
            src_s = str(edge.get("from") or edge.get("src") or "").strip()
            dst_s = str(edge.get("to") or edge.get("dst") or "").strip()
            raw_sym = edge.get("symbol") if "symbol" in edge else edge.get("on", "")
            sym_s = "" if _is_epsilon(str(raw_sym)) else str(raw_sym)
            if src_s in state_set and dst_s in state_set:
                if sym_s and sym_s not in alphabet_list:
                    alphabet_list.append(sym_s)
                trans_map.setdefault((src_s, sym_s), set()).add(dst_s)

    frozen_trans = {k: frozenset(v) for k, v in trans_map.items()}
    return Automaton(
        states=states,
        alphabet=tuple(alphabet_list),
        start=start,
        accept=accept,
        transitions=frozen_trans,
    )


def is_deterministic(aut: Automaton) -> bool:
    """Return True if automaton has no epsilon edges and <= 1 target per (q, a)."""
    for (_, sym), targets in aut.transitions.items():
        if sym == "" or len(targets) > 1:
            return False
    return True


def epsilon_closure(aut: Automaton, states: frozenset[str]) -> frozenset[str]:
    """Compute epsilon-closure of a set of states."""
    closure = set(states)
    stack = list(states)
    while stack:
        cur = stack.pop()
        for nxt in aut.transitions.get((cur, ""), ()):
            if nxt not in closure:
                closure.add(nxt)
                stack.append(nxt)
    return frozenset(closure)


def simulate_automaton(spec: dict[str, Any], word: str) -> dict[str, Any]:
    """Simulate a DFA or NFA on an input word and return step-by-step trace."""
    if not isinstance(word, str) or len(word) > MAX_WORD_LEN:
        raise ValueError(f"Vstupní slovo může mít nejvýše {MAX_WORD_LEN} znaků.")

    aut = parse_automaton(spec)
    det = is_deterministic(aut)

    current = epsilon_closure(aut, frozenset({aut.start}))
    trace: list[dict[str, Any]] = [{"step": 0, "symbol": "", "states": sorted(current)}]

    for idx, ch in enumerate(word, start=1):
        next_states: set[str] = set()
        for state in current:
            next_states.update(aut.transitions.get((state, ch), ()))
        current = epsilon_closure(aut, frozenset(next_states))
        trace.append({"step": idx, "symbol": ch, "states": sorted(current)})

    accepted = bool(current & aut.accept)
    return {
        "word": word,
        "accepted": accepted,
        "deterministic": det,
        "mode": "dfa" if det else "nfa",
        "final_states": sorted(current),
        "trace": trace,
    }


def nfa_to_complete_dfa(
    aut: Automaton,
    alphabet: tuple[str, ...],
) -> tuple[str, frozenset[str], dict[tuple[str, str], str]]:
    """Convert any DFA/NFA to a complete DFA over `alphabet` via subset construction."""
    start_subset = epsilon_closure(aut, frozenset({aut.start}))
    subset_to_name: dict[frozenset[str], str] = {}
    queue: deque[frozenset[str]] = deque([start_subset])
    subset_to_name[start_subset] = "S0"

    dfa_trans: dict[tuple[str, str], str] = {}
    dfa_accept: set[str] = set()

    while queue:
        subset = queue.popleft()
        src_name = subset_to_name[subset]
        if subset & aut.accept:
            dfa_accept.add(src_name)

        for sym in alphabet:
            next_set: set[str] = set()
            for q in subset:
                next_set.update(aut.transitions.get((q, sym), ()))
            closed = epsilon_closure(aut, frozenset(next_set))
            if closed not in subset_to_name:
                if len(subset_to_name) >= MAX_SUBSET_STATES:
                    raise ValueError("Determinizace překročila limit 256 stavů.")
                subset_to_name[closed] = f"S{len(subset_to_name)}"
                queue.append(closed)
            dfa_trans[(src_name, sym)] = subset_to_name[closed]

    return "S0", frozenset(dfa_accept), dfa_trans


def are_equivalent(spec_a: dict[str, Any], spec_b: dict[str, Any]) -> dict[str, Any]:
    """Decide language equivalence L(A) == L(B) via product DFA BFS."""
    aut_a = parse_automaton(spec_a)
    aut_b = parse_automaton(spec_b)

    union_alphabet = tuple(dict.fromkeys((*aut_a.alphabet, *aut_b.alphabet)))
    start_a, acc_a, trans_a = nfa_to_complete_dfa(aut_a, union_alphabet)
    start_b, acc_b, trans_b = nfa_to_complete_dfa(aut_b, union_alphabet)

    start_pair = (start_a, start_b)
    visited: set[tuple[str, str]] = {start_pair}
    queue: deque[tuple[str, str, str]] = deque([(start_a, start_b, "")])

    while queue:
        sa, sb, word = queue.popleft()
        in_a = sa in acc_a
        in_b = sb in acc_b
        if in_a != in_b:
            return {
                "equivalent": False,
                "counterexample": word,
                "accepted_by_a": in_a,
                "accepted_by_b": in_b,
                "tested_states": len(visited),
            }
        for sym in union_alphabet:
            na = trans_a[(sa, sym)]
            nb = trans_b[(sb, sym)]
            pair = (na, nb)
            if pair not in visited:
                visited.add(pair)
                queue.append((na, nb, word + sym))

    return {
        "equivalent": True,
        "counterexample": None,
        "accepted_by_a": None,
        "accepted_by_b": None,
        "tested_states": len(visited),
    }


PRESET_AUTOMATA: list[dict[str, Any]] = [
    {
        "id": "even_zeros",
        "title": "Sudý počet nul (DKA)",
        "description": "Přijímá binární slova nad {0, 1} se sudým počtem znaků 0.",
        "spec": {
            "states": ["q0", "q1"],
            "alphabet": ["0", "1"],
            "start": "q0",
            "accept": ["q0"],
            "transitions": {
                "q0": {"0": "q1", "1": "q0"},
                "q1": {"0": "q0", "1": "q1"},
            },
        },
        "sample_words": ["", "1010", "110", "0011"],
    },
    {
        "id": "ends_with_01",
        "title": "Slova končící 01 (DKA)",
        "description": "Přijímá všechna binární slova končící podřetězcem 01.",
        "spec": {
            "states": ["q0", "q1", "q2"],
            "alphabet": ["0", "1"],
            "start": "q0",
            "accept": ["q2"],
            "transitions": {
                "q0": {"0": "q1", "1": "q0"},
                "q1": {"0": "q1", "1": "q2"},
                "q2": {"0": "q1", "1": "q0"},
            },
        },
        "sample_words": ["01", "1001", "010", "111"],
    },
    {
        "id": "nfa_second_last_1",
        "title": "Předposlední znak je 1 (NKA)",
        "description": (
            "Nedeterministický automat přijímající slova s 1 na předposlední pozici."
        ),
        "spec": {
            "states": ["q0", "q1", "q2"],
            "alphabet": ["0", "1"],
            "start": "q0",
            "accept": ["q2"],
            "transitions": {
                "q0": {"0": ["q0"], "1": ["q0", "q1"]},
                "q1": {"0": ["q2"], "1": ["q2"]},
                "q2": {},
            },
        },
        "sample_words": ["10", "011", "101", "100"],
    },
]


def get_automata_tool_data(
    *,
    preset_id: str = "",
    word: str | None = None,
) -> dict[str, Any]:
    """Return preset automata and optional quick simulation for GET tool endpoint."""
    selected = PRESET_AUTOMATA[0]
    if preset_id:
        for item in PRESET_AUTOMATA:
            if item["id"] == preset_id:
                selected = item
                break

    simulation = (
        simulate_automaton(selected["spec"], word) if word is not None else None
    )
    return {
        "tool": "automata",
        "presets": PRESET_AUTOMATA,
        "selected": selected,
        "simulation": simulation,
    }
