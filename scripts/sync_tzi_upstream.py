#!/usr/bin/env python3
"""Sync and validate TZI question banks & glossary from upstream GitHub repo.

Public-repo safe utility: pulls ``banky/`` (including ``doplnovacka/``) and
``glosar/`` from ``zdenax/TZI-TIN-APP`` (or an explicit ``--source-dir``) into
``subjects/tzi/`` with full JSON schema validation, atomic writes, and personal
path leak scanning. Contains zero hardcoded local paths.
"""

from __future__ import annotations

import argparse
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
DEFAULT_TARGET_DIR = REPO_ROOT / "subjects" / "tzi"
UPSTREAM_REPO_URL = "https://github.com/zdenax/TZI-TIN-APP.git"
UPSTREAM_BRANCH = "main"

VALID_QUESTION_TYPES = frozenset(
    {
        "choice",
        "input",
        "input_exact",
        "input_numeric",
        "recall",
        "multi_step",
        "code_task",
        "composite_tool",
    }
)
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

LEAK_PATTERNS = (
    re.compile(r"/home/[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"file:///home/", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\(?:Users|Projects|Obsidian|Server|Documents)\\", re.I),
    re.compile(r"Obsidian\s*-\s*University", re.IGNORECASE),
)


def scan_personal_path_leaks(text: str, source_name: str) -> list[str]:
    """Return error strings if any personal filesystem path leaks appear in text."""
    errors: list[str] = []
    for pattern in LEAK_PATTERNS:
        match = pattern.search(text)
        if match:
            errors.append(
                f"{source_name}: potential personal path leak ({match.group(0)!r})"
            )
    return errors


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def validate_bank_payload(payload: Any, source_name: str) -> list[str]:
    """Validate a TZI question bank or recall bank JSON structure."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"{source_name}: top-level JSON must be an object"]

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{source_name}: missing or empty 'title'")

    if "lecture" in payload:
        lecture = payload["lecture"]
        if not isinstance(lecture, int) or isinstance(lecture, bool) or lecture < 0:
            errors.append(f"{source_name}: 'lecture' must be a non-negative integer")

    if "date" in payload and payload["date"]:
        date_val = payload["date"]
        if not isinstance(date_val, str) or not DATE_RE.fullmatch(date_val):
            errors.append(f"{source_name}: 'date' must follow YYYY-MM-DD format")

    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append(f"{source_name}: 'questions' must be a non-empty list")
        return errors

    seen_ids: set[str] = set()
    for idx, q in enumerate(questions, start=1):
        prefix = f"{source_name}[#{idx}]"
        if not isinstance(q, dict):
            errors.append(f"{prefix}: question must be an object")
            continue

        qid = str(q.get("id") or "").strip()
        if not qid:
            errors.append(f"{prefix}: missing non-empty 'id'")
        elif qid in seen_ids:
            errors.append(f"{prefix}: duplicate question id {qid!r}")
        else:
            seen_ids.add(qid)

        qtype = q.get("type")
        if qtype not in VALID_QUESTION_TYPES:
            errors.append(f"{prefix}: unsupported question type {qtype!r}")

        qtext = q.get("text")
        if not isinstance(qtext, str) or not qtext.strip():
            errors.append(f"{prefix}: missing non-empty 'text'")

        if qtype == "choice":
            options = q.get("options")
            correct = q.get("correct")
            if not isinstance(options, dict) or len(options) < 2:
                errors.append(f"{prefix}: choice requires dict 'options' (>=2)")
            if isinstance(correct, list):
                if not correct:
                    errors.append(f"{prefix}: choice 'correct' list cannot be empty")
                elif isinstance(options, dict):
                    for key in correct:
                        if key not in options:
                            errors.append(
                                f"{prefix}: correct key {key!r} not in options"
                            )
            elif not (isinstance(correct, str) and correct.strip()):
                errors.append(f"{prefix}: choice requires 'correct' list or string")
        elif qtype in {"input", "input_exact"}:
            has_answer = any(
                key in q and q[key] is not None
                for key in ("answer", "correct", "solution")
            )
            if not has_answer:
                errors.append(
                    f"{prefix}: input question requires 'answer', 'correct', "
                    "or 'solution'"
                )
        elif qtype == "recall":
            has_recall_target = (
                isinstance(q.get("accept"), list) and len(q["accept"]) > 0
            ) or (isinstance(q.get("words"), list) and len(q["words"]) > 0)
            if not has_recall_target:
                errors.append(f"{prefix}: recall requires non-empty 'accept'/'words'")

    return errors


def validate_glossary_payload(
    payload: Any,
    source_name: str,
    *,
    allow_supplements: bool = False,
) -> list[str]:
    """Validate glosar.json or doplnky.json structure."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"{source_name}: top-level JSON must be an object"]

    banks = payload.get("banks")
    if not isinstance(banks, dict) or not banks:
        return [f"{source_name}: 'banks' must be a non-empty object"]

    for bank_stem, entries in banks.items():
        if not isinstance(bank_stem, str) or not bank_stem.strip():
            errors.append(f"{source_name}: invalid empty bank key")
            continue
        if not isinstance(entries, list):
            errors.append(f"{source_name}[{bank_stem}]: entries must be a list")
            continue
        for idx, item in enumerate(entries, start=1):
            prefix = f"{source_name}[{bank_stem}#{idx}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix}: entry must be an object")
                continue
            body = item.get("body")
            if not isinstance(body, str) or not body.strip():
                errors.append(f"{prefix}: missing non-empty 'body'")
            term = item.get("term")
            attach_to = item.get("pripoj_k")
            has_term = isinstance(term, str) and bool(term.strip())
            has_attach = (
                allow_supplements
                and isinstance(attach_to, str)
                and bool(attach_to.strip())
            )
            if not (has_term or has_attach):
                errors.append(f"{prefix}: missing non-empty 'term'")

    return errors


def validate_formulas_payload(payload: Any, source_name: str) -> list[str]:
    """Validate formulas.json structure expected by webapp/subjects.py."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"{source_name}: top-level JSON must be an object"]
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        return [f"{source_name}: 'sections' must be a non-empty list"]
    for s_idx, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            errors.append(f"{source_name}[section#{s_idx}]: must be an object")
            continue
        title = section.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{source_name}[section#{s_idx}]: missing 'title'")
        items = section.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"{source_name}[section#{s_idx}]: missing 'items'")
            continue
        for i_idx, item in enumerate(items, start=1):
            prefix = f"{source_name}[section#{s_idx}.{i_idx}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix}: item must be an object")
                continue
            if not isinstance(item.get("name"), str) or not item["name"].strip():
                errors.append(f"{prefix}: missing 'name'")
            if not isinstance(item.get("latex"), str) or not item["latex"].strip():
                errors.append(f"{prefix}: missing 'latex'")
    return errors


def validate_meta_payload(payload: Any, source_name: str) -> list[str]:
    """Validate schema v2 meta.json for TZI."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"{source_name}: top-level JSON must be an object"]
    for key in ("slug", "code", "short", "title", "visibility"):
        val = payload.get(key)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"{source_name}: missing non-empty '{key}'")
    if payload.get("visibility") not in {"public", "admin"}:
        errors.append(f"{source_name}: visibility must be 'public' or 'admin'")
    if not isinstance(payload.get("engines"), list) or not payload["engines"]:
        errors.append(f"{source_name}: 'engines' must be a non-empty list")
    if not isinstance(payload.get("tools"), list):
        errors.append(f"{source_name}: 'tools' must be a list")
    return errors


def _load_and_check_json(path: Path, rel_label: str) -> tuple[Any, list[str]]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"{rel_label}: read failed ({exc})"]
    errors = scan_personal_path_leaks(raw_text, rel_label)
    try:
        payload = json.loads(raw_text)
    except ValueError as exc:
        errors.append(f"{rel_label}: invalid JSON ({exc})")
        return None, errors
    return payload, errors


def validate_subject_tree(subject_dir: Path) -> list[str]:
    """Validate all JSON files under subjects/tzi/."""
    errors: list[str] = []
    meta_path = subject_dir / "meta.json"
    if not meta_path.is_file():
        errors.append("meta.json: missing file")
    else:
        meta_payload, meta_errs = _load_and_check_json(meta_path, "meta.json")
        errors.extend(meta_errs)
        if meta_payload is not None:
            errors.extend(validate_meta_payload(meta_payload, "meta.json"))

    formulas_path = subject_dir / "formulas.json"
    if formulas_path.is_file():
        f_payload, f_errs = _load_and_check_json(formulas_path, "formulas.json")
        errors.extend(f_errs)
        if f_payload is not None:
            errors.extend(validate_formulas_payload(f_payload, "formulas.json"))

    bank_dir = subject_dir / "banky"
    bank_files = sorted(bank_dir.glob("*.json")) if bank_dir.is_dir() else []
    if not bank_files:
        errors.append("banky/: no bank JSON files found")
    for path in bank_files:
        label = f"banky/{path.name}"
        payload, file_errs = _load_and_check_json(path, label)
        errors.extend(file_errs)
        if payload is not None:
            errors.extend(validate_bank_payload(payload, label))

    recall_dir = bank_dir / "doplnovacka"
    if recall_dir.is_dir():
        for path in sorted(recall_dir.glob("*.json")):
            label = f"banky/doplnovacka/{path.name}"
            payload, file_errs = _load_and_check_json(path, label)
            errors.extend(file_errs)
            if payload is not None:
                errors.extend(validate_bank_payload(payload, label))

    glosar_path = subject_dir / "glosar" / "glosar.json"
    if not glosar_path.is_file():
        errors.append("glosar/glosar.json: missing file")
    else:
        g_payload, g_errs = _load_and_check_json(glosar_path, "glosar/glosar.json")
        errors.extend(g_errs)
        if g_payload is not None:
            errors.extend(validate_glossary_payload(g_payload, "glosar/glosar.json"))

    doplnky_path = subject_dir / "glosar" / "doplnky.json"
    if doplnky_path.is_file():
        d_payload, d_errs = _load_and_check_json(doplnky_path, "glosar/doplnky.json")
        errors.extend(d_errs)
        if d_payload is not None:
            errors.extend(
                validate_glossary_payload(
                    d_payload,
                    "glosar/doplnky.json",
                    allow_supplements=True,
                )
            )

    return errors


def _merge_missing_glossary_banks(
    incoming: dict[str, Any],
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(existing, dict):
        return incoming
    existing_banks = existing.get("banks")
    incoming_banks = incoming.get("banks")
    if not isinstance(existing_banks, dict) or not isinstance(incoming_banks, dict):
        return incoming
    merged_banks = dict(incoming_banks)
    for bank_stem, entries in existing_banks.items():
        if bank_stem not in merged_banks and isinstance(entries, list):
            merged_banks[bank_stem] = entries
    return {**incoming, "banks": merged_banks}


def sync_from_directory(
    src_dir: Path,
    dst_dir: Path,
    *,
    dry_run: bool = False,
    preserve_local_glossary: bool = True,
) -> dict[str, Any]:
    """Validate and sync banky/ and glosar/ JSON files from src_dir to dst_dir."""
    staged: list[tuple[Path, Any, str]] = []
    errors: list[str] = []

    src_banky = src_dir / "banky"
    if not src_banky.is_dir():
        raise ValueError(f"Source directory is missing banky/: {src_dir}")

    bank_files = sorted(src_banky.glob("*.json"))
    if not bank_files:
        raise ValueError(f"No bank JSON files found in {src_banky}")

    for path in bank_files:
        rel = f"banky/{path.name}"
        payload, file_errs = _load_and_check_json(path, rel)
        errors.extend(file_errs)
        if payload is not None:
            errors.extend(validate_bank_payload(payload, rel))
            staged.append((dst_dir / "banky" / path.name, payload, rel))

    src_recall = src_banky / "doplnovacka"
    if src_recall.is_dir():
        for path in sorted(src_recall.glob("*.json")):
            rel = f"banky/doplnovacka/{path.name}"
            payload, file_errs = _load_and_check_json(path, rel)
            errors.extend(file_errs)
            if payload is not None:
                errors.extend(validate_bank_payload(payload, rel))
                staged.append(
                    (dst_dir / "banky" / "doplnovacka" / path.name, payload, rel)
                )

    for g_name, allow_supplements in (
        ("glosar.json", False),
        ("doplnky.json", True),
    ):
        g_src = src_dir / "glosar" / g_name
        if not g_src.is_file():
            continue
        rel = f"glosar/{g_name}"
        payload, file_errs = _load_and_check_json(g_src, rel)
        errors.extend(file_errs)
        if payload is not None:
            errors.extend(
                validate_glossary_payload(
                    payload,
                    rel,
                    allow_supplements=allow_supplements,
                )
            )
            g_dst = dst_dir / "glosar" / g_name
            if preserve_local_glossary and g_dst.is_file():
                existing, existing_errs = _load_and_check_json(g_dst, f"local:{rel}")
                if not existing_errs and isinstance(existing, dict):
                    payload = _merge_missing_glossary_banks(payload, existing)
            staged.append((g_dst, payload, rel))

    if errors:
        raise ValueError("Validation failed:\n" + "\n".join(errors))

    added: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []

    for dst_path, payload, rel in staged:
        if dst_path.is_file():
            try:
                current = json.loads(dst_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = None
            if current is not None and _canonical_json(current) == _canonical_json(
                payload
            ):
                unchanged.append(rel)
                continue
            updated.append(rel)
        else:
            added.append(rel)

        if not dry_run:
            _atomic_write_json(dst_path, payload)

    return {
        "dry_run": dry_run,
        "total_files": len(staged),
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
    }


def _resolve_git_binary() -> str:
    git_bin = shutil.which("git")
    if not git_bin or not os.path.isabs(git_bin):
        raise RuntimeError("git executable not found in PATH")
    return git_bin


def sync_from_git(
    repo_url: str,
    branch: str,
    dst_dir: Path,
    *,
    dry_run: bool = False,
    preserve_local_glossary: bool = True,
) -> dict[str, Any]:
    """Shallow-clone upstream GitHub repository into a temp dir and sync."""
    if not repo_url.startswith("https://"):
        raise ValueError("Upstream repository URL must use https://")
    if not BRANCH_RE.fullmatch(branch) or branch.startswith("-"):
        raise ValueError(f"Invalid branch name: {branch!r}")

    git_bin = _resolve_git_binary()
    with tempfile.TemporaryDirectory(prefix="tzi-upstream-") as tmp_root:
        clone_dir = Path(tmp_root) / "repo"
        cmd = [
            git_bin,
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            "--single-branch",
            "--",
            repo_url,
            str(clone_dir),
        ]
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"git clone failed (rc={result.returncode}): {err}")
        return sync_from_directory(
            clone_dir,
            dst_dir,
            dry_run=dry_run,
            preserve_local_glossary=preserve_local_glossary,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync and validate TZI banks & glossary from upstream GitHub."
    )
    parser.add_argument(
        "--repo-url",
        default=UPSTREAM_REPO_URL,
        help="Upstream HTTPS git repository URL.",
    )
    parser.add_argument(
        "--branch",
        default=UPSTREAM_BRANCH,
        help="Upstream git branch name.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Optional local clone directory to sync from instead of cloning remote.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=DEFAULT_TARGET_DIR,
        help="Target subject directory (default: subjects/tzi).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and compute diff without writing files.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate existing target subject tree without fetching upstream.",
    )
    parser.add_argument(
        "--no-preserve-local-glossary",
        action="store_true",
        help="Overwrite local glossary banks instead of preserving extra local keys.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.validate_only:
            errors = validate_subject_tree(args.target_dir)
            report = {"valid": not errors, "errors": errors}
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            elif errors:
                for line in errors:
                    print(f"ERROR: {line}", file=sys.stderr)
            else:
                print(f"OK: {args.target_dir} validated with 0 errors.")
            return 0 if not errors else 1

        preserve = not args.no_preserve_local_glossary
        if args.source_dir is not None:
            report = sync_from_directory(
                args.source_dir,
                args.target_dir,
                dry_run=args.dry_run,
                preserve_local_glossary=preserve,
            )
        else:
            report = sync_from_git(
                args.repo_url,
                args.branch,
                args.target_dir,
                dry_run=args.dry_run,
                preserve_local_glossary=preserve,
            )
    except (RuntimeError, ValueError, OSError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))
    else:
        mode = "DRY-RUN" if report["dry_run"] else "SYNCED"
        print(
            f"[{mode}] total={report['total_files']} "
            f"added={len(report['added'])} "
            f"updated={len(report['updated'])} "
            f"unchanged={len(report['unchanged'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
