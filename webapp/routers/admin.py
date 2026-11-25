"""Isolated admin router with 404 anti-enumeration and separate template/asset root."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

import db
from webapp.config import ADMIN_UI_DIR
from webapp.csrf import verify_csrf
from webapp.routers.portal import build_base_context
from webapp.sessions import require_admin_404
from webapp.subjects import load_site_meta, load_subjects, set_subject_visibility

# require_admin_404 MUST run before verify_csrf so non-admins always get 404 (not 403).
router = APIRouter(dependencies=[Depends(require_admin_404), Depends(verify_csrf)])

_ADMIN_ASSET_ALLOWLIST: dict[str, str] = {
    "admin.css": "text/css; charset=utf-8",
    "admin.js": "application/javascript; charset=utf-8",
}

_admin_templates: Jinja2Templates | None = None


def get_admin_templates() -> Jinja2Templates:
    """Return isolated Jinja2Templates instance pointing exclusively to ui/admin."""
    global _admin_templates
    if _admin_templates is None:
        env = Environment(loader=FileSystemLoader(str(ADMIN_UI_DIR)), autoescape=True)
        _admin_templates = Jinja2Templates(env=env)
    return _admin_templates


def _build_admin_stats(all_subjects: list[dict[str, Any]]) -> dict[str, int]:
    stats = db.get_admin_stats()
    stats["subjects_public"] = sum(
        1 for s in all_subjects if s.get("visibility") == "public"
    )
    stats["subjects_admin"] = sum(
        1 for s in all_subjects if s.get("visibility") == "admin"
    )
    return stats


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    admin_user: dict[str, Any] = Depends(require_admin_404),
) -> HTMLResponse:
    site = load_site_meta()
    all_subjects = load_subjects(
        user_id=int(admin_user["id"]),
        is_admin=True,
        include_admin_fields=True,
    )
    stats = _build_admin_stats(all_subjects)
    ctx = build_base_context(
        request,
        user=admin_user,
        page_title=f"Správa | {site['title']}",
        active_nav="admin",
    )
    ctx.update(
        {
            "all_subjects": all_subjects,
            "stats": stats,
            "admin_assets_base": "/admin/assets",
        }
    )
    return get_admin_templates().TemplateResponse(request, "admin.html", ctx)


@router.get("/admin/assets/{asset_name}")
def admin_asset(asset_name: str) -> Response:
    media_type = _ADMIN_ASSET_ALLOWLIST.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Not Found")

    target = (ADMIN_UI_DIR / asset_name).resolve()
    admin_root = ADMIN_UI_DIR.resolve()
    if target.parent != admin_root or not target.is_file():
        raise HTTPException(status_code=404, detail="Not Found")

    content = target.read_bytes()
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/admin/overview")
def admin_overview(
    admin_user: dict[str, Any] = Depends(require_admin_404),
) -> dict[str, Any]:
    all_subjects = load_subjects(
        user_id=int(admin_user["id"]),
        is_admin=True,
        include_admin_fields=True,
    )
    return {
        "stats": _build_admin_stats(all_subjects),
        "subjects": all_subjects,
    }


@router.post("/api/admin/subjects/{slug}/visibility")
async def update_subject_visibility(request: Request, slug: str) -> dict[str, Any]:
    visibility_raw: str | None = None
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "application/json":
        try:
            body = await request.json()
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="Invalid JSON body.") from exc
        if isinstance(body, dict) and isinstance(body.get("visibility"), str):
            visibility_raw = body["visibility"]
    else:
        form = await request.form()
        val = form.get("visibility")
        if isinstance(val, str):
            visibility_raw = val

    if not visibility_raw:
        raise HTTPException(status_code=422, detail="Missing 'visibility' field.")

    try:
        updated = set_subject_visibility(slug, visibility_raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return {"ok": True, **updated}
