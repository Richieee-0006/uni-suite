"""Portal, subject detail, search, and health endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

import db
from webapp import config as cfg
from webapp.config import TEMPLATES_DIR
from webapp.csrf import get_or_create_csrf_token, verify_csrf
from webapp.preferences import load_user_preferences, prefs_to_html_attrs
from webapp.sessions import get_current_user, is_admin_user
from webapp.subjects import (
    get_subject_detail,
    load_site_meta,
    load_subjects,
    search_catalog,
)
from webapp.tools import (
    get_formulas_tool_data,
    get_glossary_tool_data,
    get_theory_tool_data,
)

router = APIRouter(dependencies=[Depends(verify_csrf)])

_public_templates: Jinja2Templates | None = None


def build_templates(directory: str) -> Jinja2Templates:
    """Create a Jinja2Templates instance with explicit autoescape=True."""
    env = Environment(loader=FileSystemLoader(directory), autoescape=True)
    return Jinja2Templates(env=env)


def get_public_templates() -> Jinja2Templates:
    """Return auto-escaped Jinja2Templates instance pointing to ui/templates."""
    global _public_templates
    if _public_templates is None:
        _public_templates = build_templates(str(TEMPLATES_DIR))
    return _public_templates


def build_base_context(
    request: Request,
    *,
    user: dict[str, Any] | None,
    page_title: str,
    active_nav: str,
) -> dict[str, Any]:
    """Build the shared Jinja2 template context contract."""
    prefs = load_user_preferences(user)
    user_id = (
        int(user["id"])
        if user and user.get("is_authenticated") and user.get("id") is not None
        else None
    )
    admin = is_admin_user(user)
    subjects = load_subjects(
        user_id=user_id,
        is_admin=admin,
        include_admin_fields=False,
        pinned_subjects=prefs.get("pinned_subjects"),
    )
    daily = db.get_daily_summary(user_id, daily_goal=int(prefs["daily_goal"]))
    site = load_site_meta()

    public_user: dict[str, Any] | None = None
    if user is not None:
        public_user = {
            "id": user.get("id"),
            "username": user.get("username"),
            "role": user.get("role"),
            "is_authenticated": bool(user.get("is_authenticated")),
        }

    return {
        "request": request,
        "site": site,
        "user": public_user,
        "prefs": prefs,
        "csrf_token": get_or_create_csrf_token(request),
        "subjects": subjects,
        "daily": daily,
        "page_title": page_title,
        "active_nav": active_nav,
        "html_attrs": prefs_to_html_attrs(prefs),
    }


@router.get("/", response_class=HTMLResponse)
def portal_home(request: Request) -> HTMLResponse:
    user = get_current_user(request)
    site = load_site_meta()
    ctx = build_base_context(
        request,
        user=user,
        page_title=f"Přehled předmětů | {site['title']}",
        active_nav="portal",
    )
    return get_public_templates().TemplateResponse(request, "portal.html", ctx)


def _render_subject_page(request: Request, slug: str, *, is_code_route: bool) -> Any:
    user = get_current_user(request)
    admin = is_admin_user(user)
    user_id = (
        int(user["id"])
        if user and user.get("is_authenticated") and user.get("id") is not None
        else None
    )

    detail = get_subject_detail(slug, user_id=user_id, is_admin=admin)
    if detail is None:
        raise HTTPException(status_code=404, detail="Not Found")

    subject, topics, binary_cells = detail
    if subject["kind"] == "external":
        if is_code_route:
            raise HTTPException(status_code=404, detail="Not Found")
        ext_url = subject.get("external_url")
        if isinstance(ext_url, str) and ext_url.startswith("https://"):
            return RedirectResponse(url=ext_url, status_code=302)
        raise HTTPException(status_code=404, detail="Not Found")

    error_count, due_count = db.get_subject_error_and_due_counts(
        user_id, subject["slug"]
    )
    site = load_site_meta()
    suffix = "Code Sandbox" if is_code_route else subject["title"]
    ctx = build_base_context(
        request,
        user=user,
        page_title=f"{subject['code']} — {suffix} | {site['title']}",
        active_nav=f"code:{subject['slug']}"
        if is_code_route
        else f"subject:{subject['slug']}",
    )
    ctx.update(
        {
            "subject": subject,
            "topics": topics,
            "binary_cells": binary_cells,
            "error_count": error_count,
            "due_count": due_count,
            "exam_config": subject["exam_config"],
            "is_code_route": is_code_route,
        }
    )
    return get_public_templates().TemplateResponse(request, "subject.html", ctx)


@router.get("/predmet/{slug}", response_model=None)
def subject_detail_page(request: Request, slug: str) -> Any:
    return _render_subject_page(request, slug, is_code_route=False)


@router.get("/predmet/{slug}/code", response_model=None)
def subject_code_page(request: Request, slug: str) -> Any:
    return _render_subject_page(request, slug, is_code_route=True)


@router.get("/predmet/{slug}/teorie", response_model=None)
def subject_theory_page(request: Request, slug: str) -> Any:
    user = get_current_user(request)
    admin = is_admin_user(user)
    user_id = (
        int(user["id"])
        if user and user.get("is_authenticated") and user.get("id") is not None
        else None
    )

    detail = get_subject_detail(slug, user_id=user_id, is_admin=admin)
    if detail is None:
        raise HTTPException(status_code=404, detail="Not Found")

    subject, topics, _ = detail
    if subject["kind"] == "external":
        raise HTTPException(status_code=404, detail="Not Found")

    subjects_root = cfg.SUBJECTS_DIR
    theory = get_theory_tool_data(subject["slug"], subjects_dir=subjects_root)
    formulas = get_formulas_tool_data(subject["slug"], subjects_dir=subjects_root)
    glossary = get_glossary_tool_data(subject["slug"], subjects_dir=subjects_root)
    topic_titles = {str(t["id"]): str(t["title"]) for t in topics if t.get("id")}

    site = load_site_meta()
    ctx = build_base_context(
        request,
        user=user,
        page_title=f"{subject['code']} — Teorie a glosář | {site['title']}",
        active_nav=f"theory:{subject['slug']}",
    )
    ctx.update(
        {
            "subject": subject,
            "topics": topics,
            "topic_titles": topic_titles,
            "theory": theory,
            "formulas": formulas,
            "glossary": glossary,
        }
    )
    return get_public_templates().TemplateResponse(request, "theory.html", ctx)


@router.get("/api/portal")
def api_portal(request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    prefs = load_user_preferences(user)
    user_id = (
        int(user["id"])
        if user and user.get("is_authenticated") and user.get("id") is not None
        else None
    )
    admin = is_admin_user(user)
    subjects = load_subjects(
        user_id=user_id,
        is_admin=admin,
        include_admin_fields=False,
        pinned_subjects=prefs.get("pinned_subjects"),
    )
    daily = db.get_daily_summary(user_id, daily_goal=int(prefs["daily_goal"]))

    public_user: dict[str, Any] | None = None
    if user is not None:
        public_user = {
            "id": user.get("id"),
            "username": user.get("username"),
            "role": user.get("role"),
            "is_authenticated": bool(user.get("is_authenticated")),
        }

    return {
        "site": load_site_meta(),
        "user": public_user,
        "daily": daily,
        "subjects": subjects,
        "preferences": prefs,
    }


@router.get("/api/subjects")
def api_subjects(request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    prefs = load_user_preferences(user)
    user_id = (
        int(user["id"])
        if user and user.get("is_authenticated") and user.get("id") is not None
        else None
    )
    subjects = load_subjects(
        user_id=user_id,
        is_admin=is_admin_user(user),
        include_admin_fields=False,
        pinned_subjects=prefs.get("pinned_subjects"),
    )
    return {"subjects": subjects}


@router.get("/api/search")
def api_search(
    request: Request,
    q: str = Query(default="", max_length=120),
) -> dict[str, Any]:
    user = get_current_user(request)
    results = search_catalog(q, is_admin=is_admin_user(user))
    return {"query": q.strip(), "results": results}


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
