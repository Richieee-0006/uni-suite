#!/usr/bin/env python3
"""Idempotent CLI migration from legacy TZI-TIN-APP SQLite DB to UNI Suite DB.

Migrates:
- ``users`` -> ``users`` (preserves bcrypt hashes for transparent Argon2id
  auto-upgrade on next login; normalizes ``student`` -> ``user``).
- ``user_progress`` / ``user_answers`` / ``progress`` -> ``subject_progress``
  scoped to ``subject_slug='tzi'``.
- ``daily_progress`` -> ``daily_progress``.
- ``user_scores`` / ``scores`` -> ``user_scores`` scoped to ``subject_slug='tzi'``.

NEVER migrates PvP / Arena tables (``koth_state``, ``koth_matches``, ``ffa_runs``,
``duel_matches``, ``comp_rooms``, ``comp_room_players``).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import db  # noqa: E402

SUBJECT_SLUG = "tzi"
DEFAULT_SUBJECTS_DIR = REPO_ROOT / "subjects"

IGNORED_PVP_TABLES = frozenset(
    {
        "koth_state",
        "koth_matches",
        "ffa_runs",
        "duel_matches",
        "comp_rooms",
        "comp_room_players",
    }
)
IGNORED_AUX_TABLES = frozenset({"error_logs", "question_stats"})
_PLACEHOLDER_SOURCES = frozenset({"", "_all", "_default"})


@dataclass
class _ProgressRecord:
    topic_id: str = ""
    correct_count: int = 0
    wrong_count: int = 0
    last_correct: int = 0
    lapses: int = 0
    updated_at: str = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_timestamp(raw: Any) -> str:
    if not raw:
        return _utc_now_iso()
    text = str(raw).strip()
    if not text:
        return _utc_now_iso()
    if "T" in text and text.endswith("Z"):
        return text
    if len(text) == 19 and text[10] == " ":
        return text.replace(" ", "T") + "Z"
    return text


def _normalize_role(raw_role: Any) -> str:
    return "admin" if str(raw_role or "").strip().lower() == "admin" else "user"


def _parse_status_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value or "").strip().lower()
    if text in {"correct", "ok", "true", "1", "yes"}:
        return True
    if text in {"wrong", "bad", "false", "0", "no"}:
        return False
    return None


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    allowed_tables = {
        "users",
        "user_progress",
        "progress",
        "user_answers",
        "daily_progress",
        "user_scores",
        "scores",
    }
    if table not in allowed_tables:
        raise ValueError(f"Disallowed source table inspection: {table!r}")
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _load_tzi_question_topic_map(subjects_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    tzi_banky = subjects_dir / SUBJECT_SLUG / "banky"
    for directory in (tzi_banky, tzi_banky / "doplnovacka"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            questions = payload.get("questions") if isinstance(payload, dict) else None
            if not isinstance(questions, list):
                continue
            stem = path.stem
            for idx, q in enumerate(questions, start=1):
                if isinstance(q, dict):
                    qid = str(q.get("id") or f"{stem}:{q.get('number', idx)}")
                    mapping[qid] = stem
    return mapping


def _extract_users(
    src: sqlite3.Connection,
    src_tables: set[str],
) -> list[dict[str, Any]]:
    if "users" not in src_tables:
        return []
    cols = _column_names(src, "users")
    has_role = "role" in cols
    has_created = "created_at" in cols

    select_cols = ["id", "username", "password_hash"]
    if has_role:
        select_cols.append("role")
    if has_created:
        select_cols.append("created_at")

    rows = src.execute(
        f"SELECT {', '.join(select_cols)} FROM users ORDER BY id ASC"  # noqa: S608
    ).fetchall()

    users: list[dict[str, Any]] = []
    for row in rows:
        username = str(row["username"] or "").strip()
        pw_hash = str(row["password_hash"] or "").strip()
        if not username or not pw_hash:
            continue
        users.append(
            {
                "src_id": int(row["id"]),
                "username": username,
                "password_hash": pw_hash,
                "role": _normalize_role(row["role"] if has_role else "user"),
                "created_at": _normalize_timestamp(
                    row["created_at"] if has_created else None
                ),
            }
        )
    return users


def _extract_progress(
    src: sqlite3.Connection,
    src_tables: set[str],
    qid_to_topic: dict[str, str],
) -> dict[tuple[int, str], _ProgressRecord]:
    records: dict[tuple[int, str], _ProgressRecord] = {}

    if "user_answers" in src_tables:
        a_cols = _column_names(src, "user_answers")
        has_created = "created_at" in a_cols
        created_expr = "created_at" if has_created else "''"
        rows = src.execute(
            f"SELECT user_id, question_id, is_correct, {created_expr} AS created_at "  # noqa: S608
            "FROM user_answers ORDER BY id ASC"
        ).fetchall()
        for row in rows:
            uid = int(row["user_id"])
            qid = str(row["question_id"] or "").strip()
            if not qid:
                continue
            key = (uid, qid)
            rec = records.setdefault(key, _ProgressRecord())
            is_ok = bool(int(row["is_correct"]))
            if is_ok:
                rec.correct_count += 1
                rec.last_correct = 1
            else:
                rec.wrong_count += 1
                rec.last_correct = 0
                rec.lapses += 1
            rec.updated_at = _normalize_timestamp(row["created_at"])

    if "user_progress" in src_tables:
        up_cols = _column_names(src, "user_progress")
        has_updated = "updated_at" in up_cols
        upd_expr = "updated_at" if has_updated else "''"
        rows = src.execute(
            f"SELECT user_id, source, seen_json, {upd_expr} AS updated_at "  # noqa: S608
            "FROM user_progress"
        ).fetchall()
        for row in rows:
            uid = int(row["user_id"])
            source = str(row["source"] or "").strip()
            topic_from_source = "" if source in _PLACEHOLDER_SOURCES else source
            updated_at = _normalize_timestamp(row["updated_at"])
            try:
                seen = json.loads(row["seen_json"] or "{}")
            except ValueError:
                continue
            if not isinstance(seen, dict):
                continue
            for raw_qid, raw_status in seen.items():
                qid = str(raw_qid).strip()
                status_bool = _parse_status_bool(raw_status)
                if not qid or status_bool is None:
                    continue
                key = (uid, qid)
                existed = key in records
                rec = records.setdefault(key, _ProgressRecord())
                if topic_from_source and not rec.topic_id:
                    rec.topic_id = topic_from_source
                if not existed:
                    if status_bool:
                        rec.correct_count = 1
                        rec.last_correct = 1
                    else:
                        rec.wrong_count = 1
                        rec.last_correct = 0
                        rec.lapses = 1
                    rec.updated_at = updated_at
                elif not rec.updated_at:
                    rec.updated_at = updated_at

    if "progress" in src_tables:
        p_cols = _column_names(src, "progress")
        topic_col = (
            "topic_id"
            if "topic_id" in p_cols
            else ("source" if "source" in p_cols else None)
        )
        rows = src.execute("SELECT * FROM progress").fetchall()
        for row in rows:
            row_dict = dict(row)
            uid = int(row_dict["user_id"])
            qid = str(row_dict.get("question_id") or "").strip()
            if not qid:
                continue
            key = (uid, qid)
            rec = records.setdefault(key, _ProgressRecord())
            if topic_col:
                raw_topic = str(row_dict.get(topic_col) or "").strip()
                if raw_topic and raw_topic not in _PLACEHOLDER_SOURCES:
                    rec.topic_id = raw_topic

            c_cnt = int(row_dict.get("correct_count") or 0)
            w_cnt = int(row_dict.get("wrong_count") or 0)
            status_raw = (
                row_dict.get("last_correct")
                if "last_correct" in row_dict
                else row_dict.get("is_correct", row_dict.get("status"))
            )
            status_bool = _parse_status_bool(status_raw)
            if c_cnt == 0 and w_cnt == 0 and status_bool is not None:
                if status_bool:
                    c_cnt = 1
                else:
                    w_cnt = 1

            rec.correct_count = max(rec.correct_count, c_cnt)
            rec.wrong_count = max(rec.wrong_count, w_cnt)
            if status_bool is not None:
                rec.last_correct = 1 if status_bool else 0
            elif rec.correct_count > 0 and rec.wrong_count == 0:
                rec.last_correct = 1

            lapses = row_dict.get("lapses")
            if lapses is not None:
                rec.lapses = max(rec.lapses, int(lapses))
            else:
                rec.lapses = max(rec.lapses, rec.wrong_count)
            rec.updated_at = _normalize_timestamp(row_dict.get("updated_at"))

    for (_, qid), rec in records.items():
        if not rec.topic_id and qid in qid_to_topic:
            rec.topic_id = qid_to_topic[qid]
        if not rec.updated_at:
            rec.updated_at = _utc_now_iso()

    return records


def _extract_daily_progress(
    src: sqlite3.Connection,
    src_tables: set[str],
) -> list[dict[str, Any]]:
    if "daily_progress" not in src_tables:
        return []
    cols = _column_names(src, "daily_progress")
    day_col = "activity_date" if "activity_date" in cols else "day"
    ans_col = "questions_answered" if "questions_answered" in cols else "answered"
    cor_col = "questions_correct" if "questions_correct" in cols else "correct"
    if not ({day_col, ans_col, cor_col} <= cols):
        return []

    rows = src.execute(
        f"SELECT user_id, {day_col} AS day, {ans_col} AS answered, "  # noqa: S608
        f"{cor_col} AS correct FROM daily_progress"
    ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        day = str(row["day"] or "").strip()
        if not day:
            continue
        result.append(
            {
                "src_user_id": int(row["user_id"]),
                "day": day,
                "answered": max(0, int(row["answered"] or 0)),
                "correct": max(0, int(row["correct"] or 0)),
            }
        )
    return result


def _extract_scores(
    src: sqlite3.Connection,
    src_tables: set[str],
) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []

    if "user_scores" in src_tables:
        cols = _column_names(src, "user_scores")
        rows = src.execute("SELECT * FROM user_scores").fetchall()
        for row in rows:
            d = dict(row)
            uid = int(d["user_id"])
            if "total_points" in cols and "score" not in cols:
                total_points = max(0, int(d.get("total_points") or 0))
                best_streak = max(0, int(d.get("best_streak") or 0))
                if total_points == 0 and best_streak == 0:
                    continue
                scores.append(
                    {
                        "src_user_id": uid,
                        "mode": "legacy_tzi",
                        "score": total_points,
                        "total": total_points,
                        "duration_sec": 0,
                        "created_at": _normalize_timestamp(d.get("last_active")),
                    }
                )
            elif "score" in cols:
                score_val = max(0, int(d.get("score") or 0))
                total_val = max(0, int(d.get("total") or score_val))
                scores.append(
                    {
                        "src_user_id": uid,
                        "mode": str(d.get("mode") or "legacy_tzi").strip()
                        or "legacy_tzi",
                        "score": score_val,
                        "total": total_val,
                        "duration_sec": max(0, int(d.get("duration_sec") or 0)),
                        "created_at": _normalize_timestamp(d.get("created_at")),
                    }
                )

    if "scores" in src_tables:
        rows = src.execute("SELECT * FROM scores").fetchall()
        for row in rows:
            d = dict(row)
            uid = int(d["user_id"])
            score_val = max(0, int(d.get("score") or d.get("total_points") or 0))
            total_val = max(0, int(d.get("total") or score_val))
            scores.append(
                {
                    "src_user_id": uid,
                    "mode": str(d.get("mode") or "legacy_tzi").strip() or "legacy_tzi",
                    "score": score_val,
                    "total": total_val,
                    "duration_sec": max(0, int(d.get("duration_sec") or 0)),
                    "created_at": _normalize_timestamp(
                        d.get("created_at") or d.get("last_active")
                    ),
                }
            )

    return scores


def migrate_tzi_db(
    src_path: Path,
    dst_path: Path,
    *,
    dry_run: bool = False,
    subjects_dir: Path | None = None,
) -> dict[str, Any]:
    """Migrate TZI users, progress, daily progress, and scores into UNI DB."""
    if not src_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {src_path}")

    resolved_subjects = (
        subjects_dir if subjects_dir is not None else DEFAULT_SUBJECTS_DIR
    )
    qid_to_topic = _load_tzi_question_topic_map(resolved_subjects)

    src_uri = f"file:{src_path.resolve()}?mode=ro"
    src_conn = sqlite3.connect(src_uri, uri=True)
    src_conn.row_factory = sqlite3.Row
    try:
        src_tables = _table_names(src_conn)
        ignored_pvp = sorted(src_tables & IGNORED_PVP_TABLES)
        users = _extract_users(src_conn, src_tables)
        progress_map = _extract_progress(src_conn, src_tables, qid_to_topic)
        daily_rows = _extract_daily_progress(src_conn, src_tables)
        score_rows = _extract_scores(src_conn, src_tables)
    finally:
        src_conn.close()

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "subject_slug": SUBJECT_SLUG,
        "users_created": 0,
        "users_existing": 0,
        "progress_migrated": 0,
        "daily_migrated": 0,
        "scores_migrated": 0,
        "scores_skipped": 0,
        "ignored_pvp_tables": ignored_pvp,
    }

    if dry_run:
        existing_usernames: set[str] = set()
        if dst_path.is_file():
            ro_dst = sqlite3.connect(f"file:{dst_path.resolve()}?mode=ro", uri=True)
            try:
                if "users" in _table_names(ro_dst):
                    existing_usernames = {
                        str(r[0])
                        for r in ro_dst.execute("SELECT username FROM users").fetchall()
                    }
            finally:
                ro_dst.close()

        valid_src_uids = {u["src_id"] for u in users}
        for u in users:
            if u["username"] in existing_usernames:
                report["users_existing"] += 1
            else:
                report["users_created"] += 1
        report["progress_migrated"] = sum(
            1 for (uid, _) in progress_map if uid in valid_src_uids
        )
        report["daily_migrated"] = sum(
            1 for row in daily_rows if row["src_user_id"] in valid_src_uids
        )
        report["scores_migrated"] = sum(
            1 for row in score_rows if row["src_user_id"] in valid_src_uids
        )
        return report

    db.init_db(dst_path)

    user_id_map: dict[int, int] = {}
    with db.get_connection(dst_path) as dst_conn:
        for u in users:
            existing = dst_conn.execute(
                "SELECT id FROM users WHERE username = ?",
                (u["username"],),
            ).fetchone()
            if existing is not None:
                user_id_map[u["src_id"]] = int(existing["id"])
                report["users_existing"] += 1
            else:
                cur = dst_conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        u["username"],
                        u["password_hash"],
                        u["role"],
                        u["created_at"],
                    ),
                )
                user_id_map[u["src_id"]] = int(cur.lastrowid)
                report["users_created"] += 1

        for (src_uid, qid), rec in sorted(progress_map.items()):
            dst_uid = user_id_map.get(src_uid)
            if dst_uid is None:
                continue
            dst_conn.execute(
                "INSERT INTO subject_progress ("
                "  user_id, subject_slug, topic_id, question_id, "
                "  correct_count, wrong_count, last_correct, lapses, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, subject_slug, question_id) DO UPDATE SET "
                "  topic_id = CASE "
                "    WHEN subject_progress.topic_id != '' "
                "    THEN subject_progress.topic_id "
                "    ELSE excluded.topic_id "
                "  END, "
                "  correct_count = MAX("
                "    subject_progress.correct_count, excluded.correct_count"
                "  ), "
                "  wrong_count = MAX("
                "    subject_progress.wrong_count, excluded.wrong_count"
                "  ), "
                "  last_correct = CASE "
                "    WHEN excluded.updated_at >= subject_progress.updated_at "
                "    THEN excluded.last_correct "
                "    ELSE subject_progress.last_correct "
                "  END, "
                "  lapses = MAX(subject_progress.lapses, excluded.lapses), "
                "  updated_at = MAX(subject_progress.updated_at, excluded.updated_at)",
                (
                    dst_uid,
                    SUBJECT_SLUG,
                    rec.topic_id,
                    qid,
                    rec.correct_count,
                    rec.wrong_count,
                    rec.last_correct,
                    rec.lapses,
                    rec.updated_at,
                ),
            )
            report["progress_migrated"] += 1

        for row in daily_rows:
            dst_uid = user_id_map.get(row["src_user_id"])
            if dst_uid is None:
                continue
            dst_conn.execute(
                "INSERT INTO daily_progress (user_id, day, answered, correct) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, day) DO UPDATE SET "
                "  answered = MAX(daily_progress.answered, excluded.answered), "
                "  correct = MAX(daily_progress.correct, excluded.correct)",
                (dst_uid, row["day"], row["answered"], row["correct"]),
            )
            report["daily_migrated"] += 1

        for row in score_rows:
            dst_uid = user_id_map.get(row["src_user_id"])
            if dst_uid is None:
                continue
            if row["mode"] == "legacy_tzi":
                existing_legacy = dst_conn.execute(
                    "SELECT id, score, total FROM user_scores "
                    "WHERE user_id = ? AND subject_slug = ? AND mode = 'legacy_tzi'",
                    (dst_uid, SUBJECT_SLUG),
                ).fetchone()
                if existing_legacy is not None:
                    new_score = max(int(existing_legacy["score"]), row["score"])
                    new_total = max(int(existing_legacy["total"]), row["total"])
                    if new_score != int(existing_legacy["score"]) or new_total != int(
                        existing_legacy["total"]
                    ):
                        dst_conn.execute(
                            "UPDATE user_scores SET score = ?, total = ? WHERE id = ?",
                            (new_score, new_total, existing_legacy["id"]),
                        )
                        report["scores_migrated"] += 1
                    else:
                        report["scores_skipped"] += 1
                    continue
            else:
                duplicate = dst_conn.execute(
                    "SELECT 1 FROM user_scores "
                    "WHERE user_id = ? AND subject_slug = ? AND mode = ? "
                    "AND score = ? AND total = ? AND duration_sec = ? "
                    "AND created_at = ?",
                    (
                        dst_uid,
                        SUBJECT_SLUG,
                        row["mode"],
                        row["score"],
                        row["total"],
                        row["duration_sec"],
                        row["created_at"],
                    ),
                ).fetchone()
                if duplicate is not None:
                    report["scores_skipped"] += 1
                    continue

            dst_conn.execute(
                "INSERT INTO user_scores ("
                "  user_id, subject_slug, mode, score, total, duration_sec, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    dst_uid,
                    SUBJECT_SLUG,
                    row["mode"],
                    row["score"],
                    row["total"],
                    row["duration_sec"],
                    row["created_at"],
                ),
            )
            report["scores_migrated"] += 1

        dst_conn.commit()

    db._restrict_file_permissions(dst_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Idempotently migrate TZI SQLite DB into UNI Suite SQLite DB."
    )
    parser.add_argument(
        "--src",
        type=Path,
        required=True,
        help="Path to legacy TZI-TIN-APP SQLite database (read-only).",
    )
    parser.add_argument(
        "--dst",
        type=Path,
        required=True,
        help="Path to target UNI Suite SQLite database.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect source DB and print migration counts without modifying --dst.",
    )
    parser.add_argument(
        "--subjects-dir",
        type=Path,
        default=DEFAULT_SUBJECTS_DIR,
        help="Subjects directory for question_id -> topic_id enrichment.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output migration report as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = migrate_tzi_db(
            args.src,
            args.dst,
            dry_run=args.dry_run,
            subjects_dir=args.subjects_dir,
        )
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))
    else:
        tag = "DRY-RUN" if report["dry_run"] else "MIGRATED"
        print(
            f"[{tag}] users=+{report['users_created']} "
            f"(existing={report['users_existing']}) "
            f"progress={report['progress_migrated']} "
            f"daily={report['daily_migrated']} "
            f"scores=+{report['scores_migrated']} "
            f"(skipped={report['scores_skipped']}) "
            f"ignored_pvp={report['ignored_pvp_tables']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
