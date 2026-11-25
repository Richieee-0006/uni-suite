"""Pure-Python FSRS scheduler, Chybovník error queue, and 5-minute weak-spot drill."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import db

RATING_AGAIN = 1
RATING_HARD = 2
RATING_GOOD = 3
RATING_EASY = 4

LEECH_LAPSE_THRESHOLD = 3
DEFAULT_DESIRED_RETENTION = 0.9

# FSRS-4.5-inspired compact parameters (0 external dependencies).
_INIT_STABILITY: dict[int, float] = {
    RATING_AGAIN: 0.4,
    RATING_HARD: 1.0,
    RATING_GOOD: 2.4,
    RATING_EASY: 5.8,
}
_MIN_DIFFICULTY = 1.0
_MAX_DIFFICULTY = 10.0
_DEFAULT_DIFFICULTY = 5.0
_FACTOR = 19.0 / 81.0
_DECAY = -0.5


@dataclass(frozen=True)
class FSRSState:
    """Snapshot of a card's FSRS memory state."""

    stability: float
    difficulty: float
    lapses: int
    due_at: str
    interval_days: float
    retrievability: float
    leech: bool


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _format_iso(dt: datetime) -> str:
    return _utc_now(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_leech(lapses: int) -> bool:
    """Return True when a card has reached the leech lapse threshold (>= 3)."""
    return int(lapses) >= LEECH_LAPSE_THRESHOLD


def compute_retrievability(elapsed_days: float, stability: float) -> float:
    """Compute FSRS power-law retrievability R(t, S) in [0, 1]."""
    if stability <= 0.0:
        return 0.0
    t = max(0.0, float(elapsed_days))
    return float((1.0 + _FACTOR * (t / stability)) ** _DECAY)


def _next_interval_days(stability: float, desired_retention: float) -> float:
    r = _clamp(desired_retention, 0.7, 0.98)
    raw = (stability / _FACTOR) * ((r ** (1.0 / _DECAY)) - 1.0)
    return max(0.25, raw)


def schedule_review(
    *,
    stability: float,
    difficulty: float,
    lapses: int,
    reps: int,
    rating: int,
    elapsed_days: float = 0.0,
    now: datetime | None = None,
    desired_retention: float = DEFAULT_DESIRED_RETENTION,
) -> FSRSState:
    """Compute the next FSRS state given current memory state and rating (1..4)."""
    if rating not in (RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY):
        raise ValueError("Rating must be an integer between 1 and 4.")

    ts = _utc_now(now)
    prev_s = max(0.0, float(stability))
    prev_d = _clamp(
        float(difficulty) if difficulty > 0 else _DEFAULT_DIFFICULTY,
        _MIN_DIFFICULTY,
        _MAX_DIFFICULTY,
    )
    new_lapses = max(0, int(lapses))

    if reps <= 0 or prev_s <= 0.0:
        # Initial review of a new card.
        new_s = _INIT_STABILITY[rating]
        new_d = _clamp(
            _DEFAULT_DIFFICULTY - (rating - 3) * 1.15,
            _MIN_DIFFICULTY,
            _MAX_DIFFICULTY,
        )
        retrievability = 1.0
        if rating == RATING_AGAIN:
            new_lapses += 1
            interval_days = 10.0 / 1440.0  # 10 minutes
        else:
            interval_days = max(
                1.0, round(_next_interval_days(new_s, desired_retention), 2)
            )
    else:
        t = max(0.0, float(elapsed_days))
        retrievability = compute_retrievability(t, prev_s)

        # Mean-reverting difficulty update toward D0(Good) = 5.0.
        delta_d = -(rating - 3) * 0.72
        reverted_d = 0.12 * _DEFAULT_DIFFICULTY + 0.88 * (prev_d + delta_d)
        new_d = _clamp(reverted_d, _MIN_DIFFICULTY, _MAX_DIFFICULTY)

        if rating == RATING_AGAIN:
            new_lapses += 1
            # Post-lapse stability shrinks significantly but keeps a small memory trace.
            lapse_s = (
                1.15
                * (new_d**-0.25)
                * ((prev_s + 1.0) ** 0.22)
                * math.exp(0.25 * (1.0 - retrievability))
            )
            new_s = round(_clamp(min(lapse_s, prev_s * 0.45), 0.2, 5.0), 4)
            interval_days = 10.0 / 1440.0  # Re-queue quickly in 10 minutes
        else:
            hard_penalty = 0.72 if rating == RATING_HARD else 1.0
            easy_bonus = 1.32 if rating == RATING_EASY else 1.0

            if t < 0.05:
                # Same-session review: apply bounded short-term stability growth.
                short_gain = 1.0 + (
                    0.18
                    if rating == RATING_HARD
                    else 0.38
                    if rating == RATING_GOOD
                    else 0.65
                ) * ((11.0 - new_d) / 6.0)
                new_s = prev_s * max(1.08, short_gain)
            else:
                sinc = (
                    math.exp(1.15)
                    * (11.0 - new_d)
                    * (prev_s**-0.2)
                    * (math.exp(0.85 * (1.0 - retrievability)) - 1.0)
                    * hard_penalty
                    * easy_bonus
                )
                new_s = prev_s * (1.0 + max(0.05, sinc))

            new_s = round(_clamp(new_s, 0.25, 3650.0), 4)
            base_interval = _next_interval_days(new_s, desired_retention)
            if rating == RATING_HARD:
                interval_days = max(0.5, round(base_interval * 0.75, 2))
            elif rating == RATING_EASY:
                interval_days = max(2.0, round(base_interval * 1.25, 2))
            else:
                interval_days = max(1.0, round(base_interval, 2))

    due_dt = ts + timedelta(days=interval_days)
    return FSRSState(
        stability=round(new_s, 4),
        difficulty=round(new_d, 4),
        lapses=new_lapses,
        due_at=_format_iso(due_dt),
        interval_days=round(interval_days, 4),
        retrievability=round(retrievability, 4),
        leech=is_leech(new_lapses),
    )


def record_review(
    user_id: int,
    subject_slug: str,
    question_id: str,
    *,
    topic_id: str = "",
    correct: bool,
    rating: int | None = None,
    error_reason: str | None = None,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Persist an answer review with FSRS scheduling and daily progress update."""
    ts = _utc_now(now)
    now_iso = _format_iso(ts)
    today_iso = ts.date().isoformat()

    effective_rating = (
        int(rating)
        if rating in (RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY)
        else (RATING_GOOD if correct else RATING_AGAIN)
    )
    if not correct:
        effective_rating = RATING_AGAIN

    clean_reason = (
        str(error_reason).strip()[:240] if (not correct and error_reason) else None
    )

    with db.get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT topic_id, correct_count, wrong_count, stability, difficulty, "
            "due_at, lapses, last_error_reason, updated_at "
            "FROM subject_progress "
            "WHERE user_id = ? AND subject_slug = ? AND question_id = ?",
            (user_id, subject_slug, question_id),
        ).fetchone()

        if existing is not None:
            correct_count = int(existing["correct_count"]) + (1 if correct else 0)
            wrong_count = int(existing["wrong_count"]) + (0 if correct else 1)
            prev_reps = int(existing["correct_count"]) + int(existing["wrong_count"])
            prev_stability = float(existing["stability"] or 0.0)
            prev_difficulty = float(existing["difficulty"] or _DEFAULT_DIFFICULTY)
            prev_lapses = int(existing["lapses"] or 0)
            prev_updated = _parse_iso(existing["updated_at"])
            elapsed_days = (
                max(0.0, (ts - prev_updated).total_seconds() / 86400.0)
                if prev_updated is not None
                else 0.0
            )
            stored_topic = topic_id or str(existing["topic_id"] or "")
            stored_reason = (
                clean_reason if not correct else existing["last_error_reason"]
            )
        else:
            correct_count = 1 if correct else 0
            wrong_count = 0 if correct else 1
            prev_reps = 0
            prev_stability = 0.0
            prev_difficulty = _DEFAULT_DIFFICULTY
            prev_lapses = 0
            elapsed_days = 0.0
            stored_topic = topic_id
            stored_reason = clean_reason

        state = schedule_review(
            stability=prev_stability,
            difficulty=prev_difficulty,
            lapses=prev_lapses,
            reps=prev_reps,
            rating=effective_rating,
            elapsed_days=elapsed_days,
            now=ts,
        )

        conn.execute(
            "INSERT INTO subject_progress "
            "(user_id, subject_slug, topic_id, question_id, correct_count, "
            "wrong_count, last_correct, stability, difficulty, due_at, lapses, "
            "last_error_reason, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, subject_slug, question_id) DO UPDATE SET "
            "topic_id = excluded.topic_id, "
            "correct_count = excluded.correct_count, "
            "wrong_count = excluded.wrong_count, "
            "last_correct = excluded.last_correct, "
            "stability = excluded.stability, "
            "difficulty = excluded.difficulty, "
            "due_at = excluded.due_at, "
            "lapses = excluded.lapses, "
            "last_error_reason = excluded.last_error_reason, "
            "updated_at = excluded.updated_at",
            (
                user_id,
                subject_slug,
                stored_topic,
                question_id,
                correct_count,
                wrong_count,
                1 if correct else 0,
                state.stability,
                state.difficulty,
                state.due_at,
                state.lapses,
                stored_reason,
                now_iso,
            ),
        )

        conn.execute(
            "INSERT INTO daily_progress (user_id, day, answered, correct) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET "
            "answered = daily_progress.answered + 1, "
            "correct = daily_progress.correct + excluded.correct",
            (user_id, today_iso, 1 if correct else 0),
        )
        conn.commit()

    daily = db.get_daily_summary(user_id, db_path=db_path)
    return {
        "stability": state.stability,
        "difficulty": state.difficulty,
        "due_at": state.due_at,
        "interval_days": state.interval_days,
        "retrievability": state.retrievability,
        "lapses": state.lapses,
        "leech": state.leech,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "last_correct": 1 if correct else 0,
        "daily": daily,
    }


def get_error_log_items(
    user_id: int,
    subject_slug: str | None = None,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return Chybovník error queue entries with leech flagging (lapses >= 3)."""
    with db.get_connection(db_path) as conn:
        if subject_slug is not None:
            rows = conn.execute(
                "SELECT subject_slug, topic_id, question_id, correct_count, "
                "wrong_count, last_correct, stability, difficulty, due_at, "
                "lapses, last_error_reason, updated_at "
                "FROM subject_progress "
                "WHERE user_id = ? AND subject_slug = ? "
                "AND (lapses >= 1 OR (wrong_count > 0 AND last_correct = 0)) "
                "ORDER BY (lapses >= ?) DESC, lapses DESC, "
                "wrong_count DESC, updated_at DESC",
                (user_id, subject_slug, LEECH_LAPSE_THRESHOLD),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT subject_slug, topic_id, question_id, correct_count, "
                "wrong_count, last_correct, stability, difficulty, due_at, "
                "lapses, last_error_reason, updated_at "
                "FROM subject_progress "
                "WHERE user_id = ? "
                "AND (lapses >= 1 OR (wrong_count > 0 AND last_correct = 0)) "
                "ORDER BY (lapses >= ?) DESC, lapses DESC, "
                "wrong_count DESC, updated_at DESC",
                (user_id, LEECH_LAPSE_THRESHOLD),
            ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["leech"] = is_leech(int(item.get("lapses", 0)))
        items.append(item)
    return items


def get_due_fsrs_items(
    user_id: int,
    subject_slug: str | None = None,
    *,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return FSRS cards due at or before now, ordered most overdue first."""
    now_iso = _format_iso(_utc_now(now))
    with db.get_connection(db_path) as conn:
        if subject_slug is not None:
            rows = conn.execute(
                "SELECT subject_slug, topic_id, question_id, correct_count, "
                "wrong_count, last_correct, stability, difficulty, due_at, "
                "lapses, last_error_reason, updated_at "
                "FROM subject_progress "
                "WHERE user_id = ? AND subject_slug = ? "
                "AND due_at IS NOT NULL AND due_at <= ? "
                "ORDER BY due_at ASC, lapses DESC",
                (user_id, subject_slug, now_iso),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT subject_slug, topic_id, question_id, correct_count, "
                "wrong_count, last_correct, stability, difficulty, due_at, "
                "lapses, last_error_reason, updated_at "
                "FROM subject_progress "
                "WHERE user_id = ? "
                "AND due_at IS NOT NULL AND due_at <= ? "
                "ORDER BY due_at ASC, lapses DESC",
                (user_id, now_iso),
            ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["leech"] = is_leech(int(item.get("lapses", 0)))
        items.append(item)
    return items


def select_quick_drill(
    user_id: int | None,
    visible_slugs: list[str],
    *,
    subject_slug: str | None = None,
    limit: int = 10,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Select 5-minute drill cards prioritizing leeches, overdue FSRS, and mistakes.

    Priority tiers:
      1. Leeches (`lapses >= 3`)
      2. Overdue FSRS cards (`due_at <= now`)
      3. Recent mistakes (`last_correct == 0` or `lapses >= 1`)
    """
    if user_id is None or limit <= 0:
        return []

    allowed = set(visible_slugs)
    if subject_slug is not None:
        if subject_slug not in allowed:
            return []
        allowed = {subject_slug}

    if not allowed:
        return []

    error_items = [
        row
        for row in get_error_log_items(
            user_id, subject_slug=subject_slug, db_path=db_path
        )
        if row["subject_slug"] in allowed
    ]
    due_items = [
        row
        for row in get_due_fsrs_items(
            user_id, subject_slug=subject_slug, now=now, db_path=db_path
        )
        if row["subject_slug"] in allowed
    ]

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(row: dict[str, Any], reason: str) -> None:
        if len(selected) >= limit:
            return
        key = (str(row["subject_slug"]), str(row["question_id"]))
        if key in seen:
            return
        seen.add(key)
        entry = dict(row)
        entry["priority_reason"] = reason
        entry["leech"] = is_leech(int(entry.get("lapses", 0)))
        selected.append(entry)

    # Tier 1: Leeches (lapses >= 3)
    for row in error_items:
        if row["leech"]:
            _add(row, "leech")

    # Tier 2: Overdue FSRS cards
    for row in due_items:
        _add(row, "overdue")

    # Tier 3: Remaining recent mistakes
    for row in error_items:
        _add(row, "recent_mistake")

    return selected


def save_exam_score(
    user_id: int,
    subject_slug: str,
    mode: str,
    score: int,
    total: int,
    duration_sec: int = 0,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Persist completed exam or sudden-death attempt into user_scores."""
    clean_total = max(0, int(total))
    clean_score = max(0, min(clean_total, int(score)))
    clean_duration = max(0, int(duration_sec))
    clean_mode = str(mode or "exam").strip()[:32]

    with db.get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO user_scores "
            "(user_id, subject_slug, mode, score, total, duration_sec) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                subject_slug,
                clean_mode,
                clean_score,
                clean_total,
                clean_duration,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, user_id, subject_slug, mode, score, total, "
            "duration_sec, created_at "
            "FROM user_scores WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return (
        dict(row)
        if row is not None
        else {
            "id": int(cur.lastrowid or 0),
            "user_id": user_id,
            "subject_slug": subject_slug,
            "mode": clean_mode,
            "score": clean_score,
            "total": clean_total,
            "duration_sec": clean_duration,
        }
    )
