"""User UI/study preferences API with strict allowlist validation."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

import db
from webapp.csrf import verify_csrf
from webapp.preferences import (
    load_user_preferences,
    prefs_to_html_attrs,
    validate_preferences_payload,
)
from webapp.sessions import get_current_user

router = APIRouter(dependencies=[Depends(verify_csrf)])


@router.get("/api/preferences")
def get_preferences(request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    prefs = load_user_preferences(user)
    return {
        "preferences": prefs,
        "html_attrs": prefs_to_html_attrs(prefs),
        "authenticated": bool(user and user.get("is_authenticated")),
    }


@router.put("/api/preferences")
async def update_preferences(request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    if not user or not user.get("is_authenticated") or user.get("id") is None:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        payload = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON body.") from exc

    current = load_user_preferences(user)
    try:
        updated = validate_preferences_payload(payload, base=current)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.save_user_preferences_json(
        int(user["id"]), json.dumps(updated, separators=(",", ":"))
    )
    return {
        "ok": True,
        "preferences": updated,
        "html_attrs": prefs_to_html_attrs(updated),
    }
