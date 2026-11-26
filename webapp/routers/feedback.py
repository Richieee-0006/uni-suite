"""FastAPI router for submitting user bug reports & suggestions to GitHub Issues."""

from __future__ import annotations

import json
import math
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from webapp.config import get_github_issues_config
from webapp.csrf import verify_csrf
from webapp.sandbox.base import SlidingWindowRateLimiter
from webapp.sessions import get_client_ip, get_current_user

MAX_BODY_BYTES: int = 16 * 1024
TITLE_MIN_LEN: int = 4
TITLE_MAX_LEN: int = 120
DESC_MIN_LEN: int = 10
DESC_MAX_LEN: int = 4000
CONTACT_MAX_LEN: int = 120
PAGE_URL_MAX_LEN: int = 200

_SUBJECT_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_KIND_META: dict[str, tuple[str, str, str]] = {
    "bug": ("Chyba", "bug", "Nahlášení chyby"),
    "suggestion": ("Návrh", "enhancement", "Návrh na zlepšení"),
}

router = APIRouter(prefix="/api/feedback", tags=["feedback"])
feedback_rate_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=600.0)


def _rate_limit_key(request: Request, user: dict[str, Any] | None) -> str:
    if user and user.get("is_authenticated") and user.get("id") is not None:
        return f"feedback:user:{int(user['id'])}"
    # Key guests strictly by IP so User-Agent rotation cannot bypass quotas.
    return f"feedback:ip:{get_client_ip(request)}"


def _clean_single_line(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return re.sub(r"\s+", " ", raw).strip()


def _clean_page_url(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    cleaned = raw.strip()
    if not cleaned or not cleaned.startswith("/") or cleaned.startswith("//"):
        return ""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        return ""
    return cleaned[:PAGE_URL_MAX_LEN]


async def _create_github_issue(
    *,
    token: str,
    repo: str,
    title: str,
    body: str,
    labels: list[str],
) -> tuple[int, str]:
    """Create a GitHub issue via REST API and return (issue_number, html_url)."""
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "uni-suite-feedback",
    }
    payload = {
        "title": title,
        "body": body,
        "labels": labels,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Nepodařilo se spojit s GitHub API. Zkus to prosím za chvíli.",
        ) from exc

    if resp.status_code != 201:
        raise HTTPException(
            status_code=502,
            detail="GitHub API odmítlo vytvoření issue. Zkus to prosím později.",
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Neplatná odpověď z GitHub API.",
        ) from exc

    number = data.get("number")
    html_url = data.get("html_url")
    if (
        not isinstance(number, int)
        or not isinstance(html_url, str)
        or not html_url.startswith("https://github.com/")
    ):
        raise HTTPException(
            status_code=502,
            detail="Neplatná odpověď z GitHub API.",
        )
    return number, html_url


@router.post("", dependencies=[Depends(verify_csrf)])
async def submit_feedback(request: Request) -> JSONResponse:
    """Validate user feedback and forward it to GitHub Issues."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                raise HTTPException(
                    status_code=413, detail="Požadavek je příliš velký."
                )
        except ValueError:
            pass

    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Požadavek je příliš velký.")

    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Neplatný JSON formát.") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Očekáván JSON objekt.")

    # Honeypot field silently absorbs naive bot submissions.
    if str(data.get("website") or "").strip():
        return JSONResponse({"ok": True, "issue_number": None, "issue_url": None})

    kind = str(data.get("kind") or "").strip().lower()
    if kind not in _KIND_META:
        raise HTTPException(
            status_code=422,
            detail="Vyber platný typ hlášení (chyba nebo návrh).",
        )

    title = _clean_single_line(data.get("title"))
    if len(title) < TITLE_MIN_LEN or len(title) > TITLE_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Název musí mít {TITLE_MIN_LEN} až {TITLE_MAX_LEN} znaků.",
        )

    raw_desc = data.get("description")
    description = raw_desc.strip() if isinstance(raw_desc, str) else ""
    if len(description) < DESC_MIN_LEN or len(description) > DESC_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Popis musí mít {DESC_MIN_LEN} až {DESC_MAX_LEN} znaků.",
        )

    subject = str(data.get("subject") or "").strip().lower()
    if subject and not _SUBJECT_RE.fullmatch(subject):
        raise HTTPException(status_code=422, detail="Neplatný identifikátor předmětu.")

    contact = _clean_single_line(data.get("contact"))[:CONTACT_MAX_LEN]
    page_url = _clean_page_url(data.get("page_url"))

    user = get_current_user(request)
    allowed, retry_after = feedback_rate_limiter.check(_rate_limit_key(request, user))
    if not allowed:
        retry_sec = max(1, math.ceil(retry_after))
        return JSONResponse(
            {
                "detail": (
                    f"Příliš mnoho odeslání. Zkus to prosím znovu za {retry_sec} s."
                )
            },
            status_code=429,
            headers={"Retry-After": str(retry_sec)},
        )

    gh_config = get_github_issues_config()
    if gh_config is None:
        raise HTTPException(
            status_code=503,
            detail="Odesílání na GitHub Issues není na serveru nakonfigurováno.",
        )

    token, repo = gh_config
    prefix, label, kind_label = _KIND_META[kind]
    issue_title = (
        f"[{subject.upper()}][{prefix}] {title}"
        if subject
        else f"[{prefix}] {title}"
    )

    reporter = (
        f"`{user['username']}` (přihlášený)"
        if user and user.get("is_authenticated") and user.get("username")
        else "Host (Trial)"
    )
    meta_lines = [
        f"- **Typ:** {kind_label}",
        f"- **Předmět:** `{subject}`" if subject else "- **Předmět:** Obecné / portál",
        f"- **Stránka:** `{page_url}`" if page_url else "- **Stránka:** neuvedeno",
        f"- **Uživatel:** {reporter}",
    ]
    if contact:
        meta_lines.append(f"- **Kontakt:** {contact}")

    issue_body = (
        "### Popis\n\n"
        f"{description}\n\n"
        "---\n"
        "### Kontext (odesláno z `uni.rutterle.eu`)\n"
        + "\n".join(meta_lines)
        + "\n"
    )

    issue_number, issue_url = await _create_github_issue(
        token=token,
        repo=repo,
        title=issue_title,
        body=issue_body,
        labels=[label],
    )

    return JSONResponse(
        {
            "ok": True,
            "issue_number": issue_number,
            "issue_url": issue_url,
        },
        status_code=201,
    )
