"""Loopback-only internal subject bundle endpoint for Deprecation Bridge Mode."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from webapp import subjects as subjects_mod
from webapp.config import is_testing
from webapp.subjects import SLUG_RE

router = APIRouter(tags=["internal"])

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
_FORBIDDEN_PROXY_HEADERS = (
    "cf-connecting-ip",
    "x-forwarded-for",
    "cf-ray",
    "forwarded",
)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


def _is_loopback_request(request: Request) -> bool:
    """Allow direct loopback callers only; reject any proxied/tunnelled request."""
    for header_name in _FORBIDDEN_PROXY_HEADERS:
        if request.headers.get(header_name) is not None:
            return False

    client = request.client
    host = (client.host if client is not None else "").strip().lower()
    if host in _LOOPBACK_HOSTS:
        return True
    return bool(is_testing() and host == "testclient")


def _extract_internal_token(request: Request) -> str | None:
    """Extract token from Authorization: Bearer <token> or X-Internal-Token."""
    auth_header = request.headers.get("authorization")
    if auth_header is not None:
        parts = auth_header.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            return token or None
        return None

    alt_token = request.headers.get("x-internal-token")
    if alt_token is not None:
        cleaned = alt_token.strip()
        return cleaned or None
    return None


def _verify_internal_access(request: Request) -> None:
    """Enforce fail-closed loopback + constant-time token gate (always 404)."""
    expected_token = os.getenv("INTERNAL_SYNC_TOKEN", "").strip()
    if not expected_token:
        raise _not_found()

    if not _is_loopback_request(request):
        raise _not_found()

    provided_token = _extract_internal_token(request)
    if not provided_token:
        raise _not_found()

    if not hmac.compare_digest(
        provided_token.encode("utf-8"),
        expected_token.encode("utf-8"),
    ):
        raise _not_found()


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_bank_dir(directory: Path) -> dict[str, dict[str, Any]]:
    banks: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return banks
    for path in sorted(directory.glob("*.json")):
        payload = _read_json_dict(path)
        if payload is not None:
            banks[path.stem] = payload
    return banks


def _load_glossary_bundle(subject_dir: Path) -> dict[str, Any]:
    glosar_dir = subject_dir / "glosar"
    primary = _read_json_dict(glosar_dir / "glosar.json")
    result: dict[str, Any] = dict(primary) if primary is not None else {}

    doplnky = _read_json_dict(glosar_dir / "doplnky.json")
    if doplnky is not None and isinstance(doplnky.get("banks"), dict):
        banks = result.get("banks")
        if not isinstance(banks, dict):
            banks = {}
            result["banks"] = banks
        else:
            banks = dict(banks)
            result["banks"] = banks
        for bank_stem, entries in doplnky["banks"].items():
            if bank_stem not in banks and isinstance(entries, list):
                banks[bank_stem] = entries
    return result


def compute_bundle_sha256(bundle_core: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hex digest for canonical bundle JSON."""
    canonical = json.dumps(
        bundle_core,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_subject_bundle(slug: str, subjects_dir: Path | None = None) -> dict[str, Any]:
    """Load all subject JSON data into a signed-hash bundle for bridge sync."""
    cleaned_slug = slug.strip().lower()
    if not SLUG_RE.fullmatch(cleaned_slug):
        raise _not_found()

    base_dir = subjects_dir if subjects_dir is not None else subjects_mod.SUBJECTS_DIR
    root = base_dir.resolve()
    subject_dir = (root / cleaned_slug).resolve()
    if subject_dir.parent != root or not subject_dir.is_dir():
        raise _not_found()

    meta = _read_json_dict(subject_dir / "meta.json")
    if meta is None:
        raise _not_found()

    banks = _load_bank_dir(subject_dir / "banky")
    recall_banks = _load_bank_dir(subject_dir / "banky" / "doplnovacka")
    glossary = _load_glossary_bundle(subject_dir)
    formulas = _read_json_dict(subject_dir / "formulas.json") or {"sections": []}

    bundle_core: dict[str, Any] = {
        "slug": cleaned_slug,
        "meta": meta,
        "banks": banks,
        "recall_banks": recall_banks,
        "glossary": glossary,
        "formulas": formulas,
    }
    return {
        **bundle_core,
        "sha256": compute_bundle_sha256(bundle_core),
    }


@router.get("/internal/subjects/{slug}/bundle")
def get_internal_subject_bundle(
    slug: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Return full subject JSON bundle strictly to authenticated loopback callers."""
    _verify_internal_access(request)
    bundle = build_subject_bundle(slug)
    response.headers["Cache-Control"] = "no-store"
    return bundle
