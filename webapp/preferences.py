"""Strict allowlist validator and sanitizer for user UI/study preferences."""

from __future__ import annotations

import json
import re
from typing import Any

import db

ALLOWED_THEMES = frozenset({"graphite", "amoled", "light", "catppuccin", "auto"})
ALLOWED_ACCENTS = frozenset({"sky", "emerald", "amber", "rose", "violet"})
ALLOWED_DENSITIES = frozenset({"compact", "default", "relaxed"})
ALLOWED_FONT_SCALES = frozenset({90, 100, 110, 120})
ALLOWED_RAIL_STATES = frozenset({"open", "collapsed"})
ALLOWED_CODE_LAYOUTS = frozenset({"split", "stack"})
ALLOWED_BLOCK_SIZES = frozenset({5, 10, 15, 20})
DAILY_GOAL_MIN = 3
DAILY_GOAL_MAX = 30
MAX_PINNED_SUBJECTS = 20
SLUG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

DEFAULT_PREFERENCES: dict[str, Any] = {
    "theme": "graphite",
    "accent": "sky",
    "density": "default",
    "font_scale": 100,
    "focus_mode": False,
    "show_matrix": True,
    "rail_state": "open",
    "shortcuts": True,
    "auto_advance": False,
    "sound": False,
    "code_layout": "split",
    "reduced_motion": False,
    "formula_hints": True,
    "daily_goal": 10,
    "block_size": 10,
    "pinned_subjects": [],
}

_ENUM_VALIDATORS: dict[str, frozenset[str]] = {
    "theme": ALLOWED_THEMES,
    "accent": ALLOWED_ACCENTS,
    "density": ALLOWED_DENSITIES,
    "rail_state": ALLOWED_RAIL_STATES,
    "code_layout": ALLOWED_CODE_LAYOUTS,
}

_INT_ENUM_VALIDATORS: dict[str, frozenset[int]] = {
    "font_scale": ALLOWED_FONT_SCALES,
    "block_size": ALLOWED_BLOCK_SIZES,
}

_BOOL_KEYS = frozenset(
    {
        "focus_mode",
        "show_matrix",
        "shortcuts",
        "auto_advance",
        "sound",
        "reduced_motion",
        "formula_hints",
    }
)


def _parse_strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _validate_single_field(key: str, value: Any) -> tuple[bool, Any]:
    if key in _ENUM_VALIDATORS:
        if isinstance(value, str) and value in _ENUM_VALIDATORS[key]:
            return True, value
        return False, None

    if key in _INT_ENUM_VALIDATORS:
        parsed = _parse_strict_int(value)
        if parsed is not None and parsed in _INT_ENUM_VALIDATORS[key]:
            return True, parsed
        return False, None

    if key in _BOOL_KEYS:
        if isinstance(value, bool):
            return True, value
        return False, None

    if key == "daily_goal":
        parsed = _parse_strict_int(value)
        if parsed is not None and DAILY_GOAL_MIN <= parsed <= DAILY_GOAL_MAX:
            return True, parsed
        return False, None

    if key == "pinned_subjects":
        if not isinstance(value, list) or len(value) > MAX_PINNED_SUBJECTS:
            return False, None
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not SLUG_RE.fullmatch(item):
                return False, None
            if item not in seen:
                seen.add(item)
                cleaned.append(item)
        return True, cleaned

    return False, None


def sanitize_preferences(
    raw: Any,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a clean preferences dict obeying the strict allowlist.

    Unknown keys are dropped; invalid values fall back to base/defaults.
    Raw CSS is never accepted.
    """
    result = dict(DEFAULT_PREFERENCES)
    result["pinned_subjects"] = list(DEFAULT_PREFERENCES["pinned_subjects"])

    if isinstance(base, dict):
        for key, value in base.items():
            if key in DEFAULT_PREFERENCES:
                ok, clean = _validate_single_field(key, value)
                if ok:
                    result[key] = clean

    if not isinstance(raw, dict):
        return result

    for key, value in raw.items():
        if key not in DEFAULT_PREFERENCES:
            continue
        ok, clean = _validate_single_field(key, value)
        if ok:
            result[key] = clean

    return result


def validate_preferences_payload(
    raw: Any,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an incoming API preference update payload.

    Unknown keys are silently dropped; invalid values for known keys raise ValueError.
    """
    if not isinstance(raw, dict):
        raise ValueError("Preferences payload must be a JSON object.")

    result = sanitize_preferences(base)
    errors: list[str] = []

    for key, value in raw.items():
        if key not in DEFAULT_PREFERENCES:
            # Unknown keys (including attempted custom_css/style) are strictly dropped.
            continue
        ok, clean = _validate_single_field(key, value)
        if not ok:
            errors.append(f"Invalid value for preference '{key}'.")
            continue
        result[key] = clean

    if errors:
        raise ValueError(" ".join(errors))

    return result


def parse_preferences_json(raw_json: str | None) -> dict[str, Any]:
    """Safely parse and sanitize JSON text from the database."""
    if not raw_json:
        return sanitize_preferences({})
    try:
        parsed = json.loads(raw_json)
    except (ValueError, TypeError):
        return sanitize_preferences({})
    return sanitize_preferences(parsed)


def load_user_preferences(user: dict[str, Any] | None) -> dict[str, Any]:
    """Load sanitized preferences for an authenticated user or guest defaults."""
    if not user or not user.get("is_authenticated") or user.get("id") is None:
        return sanitize_preferences({})
    raw_json = db.get_user_preferences_json(int(user["id"]))
    return parse_preferences_json(raw_json)


def prefs_to_html_attrs(prefs: dict[str, Any]) -> dict[str, str]:
    """Project validated preferences into HTML data-* attributes (zero inline CSS)."""
    clean = sanitize_preferences(prefs)
    return {
        "data-theme": str(clean["theme"]),
        "data-accent": str(clean["accent"]),
        "data-density": str(clean["density"]),
        "data-font-scale": str(clean["font_scale"]),
        "data-focus": "1" if clean["focus_mode"] else "0",
        "data-matrix": "1" if clean["show_matrix"] else "0",
        "data-rail": str(clean["rail_state"]),
        "data-shortcuts": "1" if clean["shortcuts"] else "0",
        "data-auto-advance": "1" if clean["auto_advance"] else "0",
        "data-sound": "1" if clean["sound"] else "0",
        "data-code-layout": str(clean["code_layout"]),
        "data-reduced-motion": "1" if clean["reduced_motion"] else "0",
    }
