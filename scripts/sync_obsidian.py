#!/usr/bin/env python3
"""Obsidian-to-UNI-Suite content synchronization & schema validation CLI.

Public-repo safe utility: reads the Obsidian vault path strictly from
``--vault`` or the ``OBSIDIAN_VAULT`` environment variable, never embeds
local filesystem paths into generated JSON files, supports incremental sync
of glossary terms and formula items for ``num`` and ``pas``, validates all
bank/glossary/formula JSON schemas, and can verify R ``code_task`` solutions
with ``Rscript``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBJECTS_DIR = REPO_ROOT / "subjects"
SUPPORTED_SUBJECTS = ("num", "pas")

SUBJECT_VAULT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "num": ("Semestr 3/NUM", "NUM"),
    "pas": ("Semestr 3/PAS", "PAS"),
}

DEFAULT_GLOSSARY_BUCKET: dict[str, str] = {
    "num": "01-chyby-a-aritmetika",
    "pas": "01-pravdepodobnost-a-nahodne-veliciny",
}

VALID_QUESTION_TYPES = {"choice", "input_numeric", "recall", "multi_step", "code_task"}
VALID_TOLERANCE_MODES = {"abs", "rel"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEAK_PATTERNS = (
    re.compile(r"/home/[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"C:\\Users\\", re.IGNORECASE),
    re.compile(r"Obsidian\s*-\s*University", re.IGNORECASE),
    re.compile(r"file:///home/", re.IGNORECASE),
)

BOLD_BULLET_RE = re.compile(
    r"^\s*[-*]\s+\*\*(?P<term>[^*]{2,80}?)\*\*\s*:?\s+(?P<body>.+)$"
)
DISPLAY_MATH_RE = re.compile(r"\$\$(?P<latex>.+?)\$\$", re.DOTALL)
HEADING_RE = re.compile(r"^#{1,3}\s+(?P<title>.+?)\s*$")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def normalize_key(text: str) -> str:
    """Normalize Czech/ASCII label for case-insensitive deduplication."""
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    return cleaned.rstrip(":;.,")


def sanitize_markdown_text(text: str) -> str:
    """Strip markdown file links and excess whitespace while preserving math."""
    text = MD_LINK_RE.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def is_tldraw_markdown(content: str) -> bool:
    """Return True if a markdown file is an Obsidian tldraw canvas."""
    head = content[:400]
    return "tldraw-file: true" in head or "START_OF_TLDRAW_DATA" in head


def resolve_subject_vault_dir(vault_root: Path, slug: str) -> Path | None:
    """Locate the subject directory inside the user-supplied Obsidian vault."""
    for rel in SUBJECT_VAULT_CANDIDATES[slug]:
        candidate = vault_root / rel
        if candidate.is_dir():
            return candidate
    return None


def collect_markdown_notes(subject_vault_dir: Path) -> list[Path]:
    """Collect readable markdown theory notes, skipping tldraw & HTML."""
    notes: list[Path] = []
    for sub in ("Teorie", "Semináře", "."):
        folder = subject_vault_dir / sub if sub != "." else subject_vault_dir
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name.lower() in {"readme.md", "intro.md"}:
                continue
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:500]
            except OSError:
                continue
            if is_tldraw_markdown(head) or head.lstrip().startswith("<!DOCTYPE html"):
                continue
            if path not in notes:
                notes.append(path)
    return notes


def extract_obsidian_items(
    notes: list[Path],
) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    """Extract glossary terms, formula items, and content digest from notes."""
    glossary_items: list[dict[str, str]] = []
    formula_items: list[dict[str, str]] = []
    seen_terms: set[str] = set()
    seen_latex: set[str] = set()
    hasher = hashlib.sha256()

    for note in notes:
        raw = note.read_text(encoding="utf-8", errors="ignore")
        hasher.update(raw.encode("utf-8"))
        current_heading = "Přehled"
        for line in raw.splitlines():
            h_match = HEADING_RE.match(line)
            if h_match:
                current_heading = sanitize_markdown_text(h_match.group("title"))
                continue
            b_match = BOLD_BULLET_RE.match(line)
            if b_match:
                term = sanitize_markdown_text(b_match.group("term")).rstrip(":")
                body = sanitize_markdown_text(b_match.group("body"))
                key = normalize_key(term)
                if term and body and len(body) >= 12 and key not in seen_terms:
                    seen_terms.add(key)
                    glossary_items.append({"term": term, "body": body})
        for match in DISPLAY_MATH_RE.finditer(raw):
            latex = re.sub(r"\s+", " ", match.group("latex").strip())
            if not latex or len(latex) < 4 or latex in seen_latex:
                continue
            seen_latex.add(latex)
            formula_items.append(
                {
                    "name": current_heading,
                    "latex": latex,
                    "note": "Synchronizováno ze studijních poznámek.",
                }
            )

    return glossary_items, formula_items, hasher.hexdigest()[:16]


def check_no_personal_leaks(path: Path, raw_text: str) -> list[str]:
    """Ensure generated JSON/files contain zero personal or local paths."""
    errors: list[str] = []
    rel = path.relative_to(REPO_ROOT)
    for pat in LEAK_PATTERNS:
        if pat.search(raw_text):
            errors.append(f"{rel}: matched forbidden path pattern {pat.pattern!r}")
    return errors


def _is_non_empty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


def _is_numeric(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def validate_hints(q_ref: str, question: dict[str, Any]) -> list[str]:
    """Validate 3-step progressive hints array and explanation."""
    errors: list[str] = []
    hints = question.get("hints")
    if (
        not isinstance(hints, list)
        or len(hints) != 3
        or not all(_is_non_empty_str(h) for h in hints)
    ):
        errors.append(f"{q_ref}: 'hints' must be a list of 3 non-empty strings")
    if not _is_non_empty_str(question.get("explanation")):
        errors.append(f"{q_ref}: 'explanation' must be a non-empty string")
    return errors


def _validate_choice_fields(ref: str, obj: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    opts = obj.get("options")
    correct = obj.get("correct")
    if not isinstance(opts, dict) or len(opts) < 2:
        errors.append(f"{ref}: 'options' must be a dict with >=2 choices")
    if not isinstance(correct, list) or not correct:
        errors.append(f"{ref}: 'correct' must be a non-empty list")
    elif isinstance(opts, dict):
        for ans in correct:
            if ans not in opts:
                errors.append(f"{ref}: correct key {ans!r} not found in options")
    return errors


def _validate_numeric_fields(
    ref: str,
    obj: dict[str, Any],
    require_unit: bool,
) -> list[str]:
    errors: list[str] = []
    if not _is_numeric(obj.get("answer")):
        errors.append(f"{ref}: 'answer' must be numeric")
    tol = obj.get("tolerance")
    if not _is_numeric(tol) or tol < 0:
        errors.append(f"{ref}: 'tolerance' must be non-negative numeric")
    if obj.get("tolerance_mode") not in VALID_TOLERANCE_MODES:
        errors.append(f"{ref}: 'tolerance_mode' must be 'abs' or 'rel'")
    if require_unit and not isinstance(obj.get("unit"), str):
        errors.append(f"{ref}: 'unit' must be a string")
    return errors


def _validate_code_task(q_ref: str, q: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("language", "title", "formula", "starter_code", "solution_code"):
        if not _is_non_empty_str(q.get(field)):
            errors.append(f"{q_ref}: code_task requires non-empty '{field}'")
    if not isinstance(q.get("produces_plot"), bool):
        errors.append(f"{q_ref}: code_task requires boolean 'produces_plot'")
    for group in ("visible_tests", "hidden_tests"):
        tests = q.get(group)
        if not isinstance(tests, list) or not tests:
            errors.append(f"{q_ref}: code_task requires non-empty '{group}'")
            continue
        for t_idx, test in enumerate(tests):
            t_ref = f"{q_ref}.{group}[{t_idx}]"
            if not isinstance(test, dict):
                errors.append(f"{t_ref}: test must be an object")
                continue
            if not _is_non_empty_str(test.get("name")):
                errors.append(f"{t_ref}: missing 'name'")
            if not _is_non_empty_str(test.get("code")):
                errors.append(f"{t_ref}: missing 'code'")
            if "expected" not in test:
                errors.append(f"{t_ref}: missing 'expected'")
            tol = test.get("tolerance")
            if not _is_numeric(tol) or tol < 0:
                errors.append(f"{t_ref}: 'tolerance' must be non-negative numeric")
    return errors


def validate_question(
    bank_rel: str,
    idx: int,
    q: dict[str, Any],
    seen_ids: set[str],
) -> list[str]:
    """Validate one question against the TZI v1 + uni-engine v2 contract."""
    errors: list[str] = []
    q_ref = f"{bank_rel}#q{idx + 1}"
    if not isinstance(q, dict):
        return [f"{q_ref}: question must be an object"]

    qid = q.get("id")
    if not _is_non_empty_str(qid):
        errors.append(f"{q_ref}: missing string 'id'")
    elif qid in seen_ids:
        errors.append(f"{q_ref}: duplicate question id {qid!r}")
    else:
        seen_ids.add(qid)

    if not isinstance(q.get("number"), int) or q["number"] < 1:
        errors.append(f"{q_ref}: 'number' must be a positive integer")

    qtype = q.get("type")
    if qtype not in VALID_QUESTION_TYPES:
        errors.append(f"{q_ref}: invalid type {qtype!r}")
        return errors

    if not _is_non_empty_str(q.get("text")):
        errors.append(f"{q_ref}: 'text' must be a non-empty string")

    errors.extend(validate_hints(q_ref, q))

    if qtype == "choice":
        errors.extend(_validate_choice_fields(q_ref, q))
    elif qtype == "input_numeric":
        errors.extend(_validate_numeric_fields(q_ref, q, require_unit=True))
    elif qtype == "recall":
        if not _is_non_empty_str(q.get("category")):
            errors.append(f"{q_ref}: recall 'category' must be non-empty")
        accept = q.get("accept")
        if (
            not isinstance(accept, list)
            or not accept
            or not all(_is_non_empty_str(a) for a in accept)
        ):
            errors.append(f"{q_ref}: recall 'accept' must be non-empty list")
        if not isinstance(q.get("words"), int) or q["words"] < 1:
            errors.append(f"{q_ref}: recall 'words' must be a positive integer")
    elif qtype == "multi_step":
        steps = q.get("steps")
        if not isinstance(steps, list) or len(steps) < 2:
            errors.append(f"{q_ref}: multi_step 'steps' must have >=2 steps")
        else:
            for s_idx, step in enumerate(steps):
                s_ref = f"{q_ref}.step{s_idx + 1}"
                if not isinstance(step, dict):
                    errors.append(f"{s_ref}: step must be an object")
                    continue
                if not _is_non_empty_str(step.get("prompt")):
                    errors.append(f"{s_ref}: missing 'prompt'")
                stype = step.get("type")
                if stype == "choice":
                    errors.extend(_validate_choice_fields(s_ref, step))
                elif stype == "input_numeric":
                    errors.extend(
                        _validate_numeric_fields(s_ref, step, require_unit=False)
                    )
                else:
                    errors.append(
                        f"{s_ref}: step type must be 'choice' or 'input_numeric'"
                    )
    elif qtype == "code_task":
        errors.extend(_validate_code_task(q_ref, q))

    return errors


def validate_bank_file(
    path: Path,
    seen_ids: set[str],
    expect_recall: bool = False,
) -> list[str]:
    """Validate a single question bank JSON file."""
    errors: list[str] = []
    rel = str(path.relative_to(REPO_ROOT))
    raw = path.read_text(encoding="utf-8")
    errors.extend(check_no_personal_leaks(path, raw))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{rel}: invalid JSON: {exc}"]

    if not isinstance(data, dict):
        return [f"{rel}: root must be an object"]
    if not _is_non_empty_str(data.get("title")):
        errors.append(f"{rel}: missing non-empty string 'title'")
    if not isinstance(data.get("lecture"), int) or data["lecture"] < 1:
        errors.append(f"{rel}: 'lecture' must be positive int")
    date_val = data.get("date")
    if not isinstance(date_val, str) or not DATE_RE.match(date_val):
        errors.append(f"{rel}: 'date' must match YYYY-MM-DD")

    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append(f"{rel}: 'questions' must be a non-empty list")
        return errors

    for idx, q in enumerate(questions):
        errors.extend(validate_question(rel, idx, q, seen_ids))
        if expect_recall and isinstance(q, dict) and q.get("type") != "recall":
            errors.append(f"{rel}#q{idx + 1}: doplnovacka question must be 'recall'")
    return errors


def validate_glossary(path: Path) -> list[str]:
    """Validate subjects/<slug>/glosar/glosar.json."""
    errors: list[str] = []
    rel = str(path.relative_to(REPO_ROOT))
    if not path.is_file():
        return [f"{rel}: missing glossary file"]
    raw = path.read_text(encoding="utf-8")
    errors.extend(check_no_personal_leaks(path, raw))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{rel}: invalid JSON: {exc}"]

    banks = data.get("banks") if isinstance(data, dict) else None
    if not isinstance(banks, dict) or not banks:
        return [f"{rel}: 'banks' must be a non-empty object"]
    for stem, entries in banks.items():
        if not isinstance(entries, list) or not entries:
            errors.append(f"{rel}: bank {stem!r} must map to a non-empty list")
            continue
        for idx, entry in enumerate(entries):
            if (
                not isinstance(entry, dict)
                or not _is_non_empty_str(entry.get("term"))
                or not _is_non_empty_str(entry.get("body"))
            ):
                errors.append(f"{rel}: invalid glossary entry at {stem}[{idx}]")
    return errors


def validate_formulas(path: Path) -> list[str]:
    """Validate subjects/<slug>/formulas.json."""
    errors: list[str] = []
    rel = str(path.relative_to(REPO_ROOT))
    if not path.is_file():
        return [f"{rel}: missing formulas file"]
    raw = path.read_text(encoding="utf-8")
    errors.extend(check_no_personal_leaks(path, raw))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{rel}: invalid JSON: {exc}"]

    sections = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections, list) or not sections:
        return [f"{rel}: 'sections' must be a non-empty list"]
    for s_idx, sec in enumerate(sections):
        if not isinstance(sec, dict) or not _is_non_empty_str(sec.get("title")):
            errors.append(f"{rel}: section[{s_idx}] missing 'title'")
            continue
        items = sec.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"{rel}: section[{s_idx}] missing non-empty 'items'")
            continue
        for i_idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(
                    f"{rel}: section[{s_idx}].items[{i_idx}] must be an object"
                )
                continue
            for field in ("name", "latex", "note"):
                if not _is_non_empty_str(item.get(field)):
                    errors.append(
                        f"{rel}: section[{s_idx}].items[{i_idx}] missing '{field}'"
                    )
    return errors


def _build_r_check_expr(expected: Any, tol: float) -> str | None:
    if isinstance(expected, bool):
        exp_r = "TRUE" if expected else "FALSE"
        return f"identical(as.logical(res), {exp_r})"
    if isinstance(expected, (int, float)):
        return (
            "is.numeric(res) && length(res) == 1 && !is.na(res) && "
            f"abs(as.numeric(res) - ({expected!r})) <= ({tol!r})"
        )
    if isinstance(expected, list):
        vec_r = "c(" + ", ".join(repr(v) for v in expected) + ")"
        return (
            f"is.numeric(res) && length(res) == length({vec_r}) && "
            f"all(abs(as.numeric(res) - {vec_r}) <= ({tol!r}))"
        )
    return None


def verify_r_code_task(
    rscript_bin: str,
    bank_rel: str,
    task: dict[str, Any],
) -> list[str]:
    """Execute an R code_task solution_code and all tests via Rscript."""
    errors: list[str] = []
    qid = task.get("id", "unknown")
    solution_code = task["solution_code"]
    produces_plot = bool(task.get("produces_plot"))
    all_tests = [("visible", t) for t in task.get("visible_tests", [])] + [
        ("hidden", t) for t in task.get("hidden_tests", [])
    ]

    with tempfile.TemporaryDirectory(prefix="uni_r_verify_") as tmpdir:
        tmp_path = Path(tmpdir)
        plot_pattern = (tmp_path / "plot_%02d.png").as_posix()
        runner_lines = [
            f'png(filename = "{plot_pattern}", width = 640, height = 420)',
            solution_code,
            "dev.off()",
        ]
        for group, test in all_tests:
            tname = test["name"].replace('"', '\\"')
            tcode = test["code"]
            expected = test["expected"]
            tol = float(test["tolerance"])
            check_expr = _build_r_check_expr(expected, tol)
            if check_expr is None:
                errors.append(
                    f"{bank_rel}#{qid}: unsupported expected type in {test['name']!r}"
                )
                continue
            fail_msg = (
                f'FAIL [{group}] {tname}: got=%s expected={expected!r} tol={tol!r}'
            )
            runner_lines.append(
                "local({\n"
                f"  res <- ({tcode})\n"
                f"  ok <- ({check_expr})\n"
                f'  if (!isTRUE(ok)) stop(sprintf("{fail_msg}", '
                'paste(res, collapse=",")))\n'
                "})"
            )

        script_path = tmp_path / "verify.R"
        script_path.write_text("\n".join(runner_lines) + "\n", encoding="utf-8")

        proc = subprocess.run(  # noqa: S603
            [rscript_bin, "--vanilla", str(script_path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            err_lines = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
            tail = " | ".join(err_lines)
            errors.append(f"{bank_rel}#{qid}: Rscript failed -> {tail}")
            return errors

        pngs = sorted(tmp_path.glob("plot_*.png"))
        if produces_plot:
            if not pngs:
                errors.append(
                    f"{bank_rel}#{qid}: produces_plot=true but no PNG was generated"
                )
            elif pngs[0].read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                errors.append(f"{bank_rel}#{qid}: generated plot is not a valid PNG")
    return errors


def validate_subject(
    slug: str,
    check_r: bool = False,
) -> tuple[list[str], dict[str, int]]:
    """Validate all bank, glossary, and formula files for one subject."""
    subj_dir = SUBJECTS_DIR / slug
    banky_dir = subj_dir / "banky"
    dopl_dir = banky_dir / "doplnovacka"
    errors: list[str] = []
    seen_ids: set[str] = set()
    stats = {"banks": 0, "questions": 0, "code_tasks": 0, "recall": 0}

    bank_files = sorted(banky_dir.glob("*.json")) if banky_dir.is_dir() else []
    if len(bank_files) < 5:
        errors.append(
            f"subjects/{slug}/banky: expected >=5 banks, found {len(bank_files)}"
        )

    rscript_bin = shutil.which("Rscript") if check_r else None
    if check_r and not rscript_bin:
        errors.append("Rscript executable not found in PATH for --check-r")

    for bf in bank_files:
        stats["banks"] += 1
        b_errs = validate_bank_file(bf, seen_ids, expect_recall=False)
        errors.extend(b_errs)
        if not b_errs:
            data = json.loads(bf.read_text(encoding="utf-8"))
            qs = data["questions"]
            stats["questions"] += len(qs)
            for q in qs:
                if q.get("type") == "code_task":
                    stats["code_tasks"] += 1
                    if check_r and rscript_bin and q.get("language") == "r":
                        rel = str(bf.relative_to(REPO_ROOT))
                        errors.extend(verify_r_code_task(rscript_bin, rel, q))

    dopl_files = sorted(dopl_dir.glob("*.json")) if dopl_dir.is_dir() else []
    if not dopl_files:
        errors.append(f"subjects/{slug}/banky/doplnovacka: expected >=1 recall bank")
    for df in dopl_files:
        d_errs = validate_bank_file(df, seen_ids, expect_recall=True)
        errors.extend(d_errs)
        if not d_errs:
            data = json.loads(df.read_text(encoding="utf-8"))
            stats["recall"] += len(data["questions"])

    errors.extend(validate_glossary(subj_dir / "glosar" / "glosar.json"))
    errors.extend(validate_formulas(subj_dir / "formulas.json"))
    return errors, stats


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def incremental_sync_subject(
    slug: str,
    vault_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Incrementally merge new Obsidian terms/formulas into subject JSONs."""
    subject_vault = resolve_subject_vault_dir(vault_root, slug)
    if subject_vault is None:
        return {"slug": slug, "synced": False, "reason": "subject folder not found"}

    notes = collect_markdown_notes(subject_vault)
    ext_glossary, ext_formulas, digest = extract_obsidian_items(notes)

    glosar_path = SUBJECTS_DIR / slug / "glosar" / "glosar.json"
    formulas_path = SUBJECTS_DIR / slug / "formulas.json"

    glosar = (
        json.loads(glosar_path.read_text(encoding="utf-8"))
        if glosar_path.is_file()
        else {"banks": {}}
    )
    existing_terms = {
        normalize_key(entry["term"])
        for entries in glosar.get("banks", {}).values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict) and "term" in entry
    }

    bucket_key = DEFAULT_GLOSSARY_BUCKET[slug]
    sync_bucket = glosar.setdefault("banks", {}).setdefault(bucket_key, [])
    added_terms = 0
    for item in ext_glossary:
        key = normalize_key(item["term"])
        if key not in existing_terms:
            existing_terms.add(key)
            sync_bucket.append(item)
            added_terms += 1

    if added_terms and not dry_run:
        _write_json(glosar_path, glosar)

    formulas = (
        json.loads(formulas_path.read_text(encoding="utf-8"))
        if formulas_path.is_file()
        else {"sections": []}
    )
    existing_latex = {
        re.sub(r"\s+", "", item.get("latex", ""))
        for sec in formulas.get("sections", [])
        if isinstance(sec, dict)
        for item in sec.get("items", [])
        if isinstance(item, dict)
    }

    new_formula_items = [
        f for f in ext_formulas if re.sub(r"\s+", "", f["latex"]) not in existing_latex
    ]
    added_formulas = len(new_formula_items)
    if new_formula_items and not dry_run:
        target_sec = next(
            (s for s in formulas["sections"] if s.get("title") == "Doplňkové vzorce"),
            None,
        )
        if target_sec is None:
            target_sec = {"title": "Doplňkové vzorce", "items": []}
            formulas["sections"].append(target_sec)
        target_sec["items"].extend(new_formula_items)
        _write_json(formulas_path, formulas)

    return {
        "slug": slug,
        "synced": True,
        "notes_scanned": len(notes),
        "digest": digest,
        "added_terms": added_terms,
        "added_formulas": added_formulas,
        "dry_run": dry_run,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Incrementally sync Obsidian notes and validate subject JSONs."
    )
    parser.add_argument(
        "--vault",
        type=str,
        default=os.environ.get("OBSIDIAN_VAULT", ""),
        help="Path to Obsidian vault directory (or set OBSIDIAN_VAULT env var).",
    )
    parser.add_argument(
        "--subject",
        choices=("all", *SUPPORTED_SUBJECTS),
        default="all",
        help="Subject to sync/validate (default: all).",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Skip Obsidian sync and only validate subject JSON schemas.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report incremental sync changes without modifying JSON files.",
    )
    parser.add_argument(
        "--check-r",
        action="store_true",
        help="Execute every R code_task solution_code and all tests via Rscript.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    slugs = SUPPORTED_SUBJECTS if args.subject == "all" else (args.subject,)

    if not args.validate_only:
        if not args.vault:
            print(
                "Error: --vault or OBSIDIAN_VAULT is required "
                "unless --validate-only is set.",
                file=sys.stderr,
            )
            return 2
        vault_path = Path(args.vault).expanduser().resolve()
        if not vault_path.is_dir():
            print(
                f"Error: Obsidian vault directory not found: {args.vault}",
                file=sys.stderr,
            )
            return 2
        for slug in slugs:
            report = incremental_sync_subject(slug, vault_path, dry_run=args.dry_run)
            print(
                f"[sync:{slug}] notes={report.get('notes_scanned', 0)} "
                f"added_terms={report.get('added_terms', 0)} "
                f"added_formulas={report.get('added_formulas', 0)} "
                f"digest={report.get('digest', '-')} dry_run={args.dry_run}"
            )

    all_errors: list[str] = []
    for slug in slugs:
        errors, stats = validate_subject(slug, check_r=args.check_r)
        all_errors.extend(errors)
        status = "OK" if not errors else f"FAIL ({len(errors)} errors)"
        print(
            f"[validate:{slug}] {status} | banks={stats['banks']} "
            f"questions={stats['questions']} "
            f"code_tasks={stats['code_tasks']} recall={stats['recall']}"
        )

    if all_errors:
        for err in all_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
