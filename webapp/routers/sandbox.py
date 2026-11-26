"""FastAPI router for Tier 2 isolated sandbox execution & driver discovery."""

from __future__ import annotations

import json
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from webapp.csrf import verify_csrf
from webapp.sandbox.base import (
    DriverNotFoundError,
    ExecutionRequest,
    RateLimitError,
    SandboxLimits,
    SlidingWindowRateLimiter,
    TestCase,
    ValidationError,
)
from webapp.sandbox.registry import default_registry
from webapp.sessions import get_client_ip, get_current_user

MAX_BODY_BYTES: int = 64 * 1024  # 64 KB raw HTTP request cap

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])
sandbox_rate_limiter = SlidingWindowRateLimiter()
_limits = SandboxLimits()


def _rate_limit_key(request: Request, user: dict[str, Any]) -> str:
    if user.get("is_authenticated") and user.get("id") is not None:
        return f"user:{int(user['id'])}"
    # Key trial runs strictly by IP so User-Agent rotation cannot bypass quotas.
    return f"trial:ip:{get_client_ip(request)}"


def _error_payload(status: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "stdout": "",
        "stderr": message,
        "tests": [],
        "table_diff": None,
        "plots_b64": [],
        "elapsed_ms": 0,
    }


@router.get("/drivers")
async def get_sandbox_drivers() -> dict[str, Any]:
    """Return available Tier 1 browser workers, Tier 2 Docker drivers, and limits."""
    catalog = default_registry.list_all()
    catalog["limits"] = _limits.to_dict()
    return catalog


@router.post("/run", dependencies=[Depends(verify_csrf)])
async def run_sandbox_code(request: Request) -> JSONResponse:
    """Execute code in an isolated Tier 2 Docker driver with strict security limits."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Pro spuštění sandboxu se přihlaste nebo zapněte Trial.",
        )

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content=_error_payload(
                        "validation_error",
                        "Požadavek překračuje maximální povolenou velikost.",
                    ),
                )
        except ValueError:
            pass

    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content=_error_payload(
                "validation_error",
                "Požadavek překračuje maximální povolenou velikost.",
            ),
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            status_code=400,
            content=_error_payload(
                "validation_error",
                "Neplatný JSON formát požadavku.",
            ),
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content=_error_payload(
                "validation_error",
                "Tělo požadavku musí být JSON objekt.",
            ),
        )

    try:
        sandbox_rate_limiter.check_or_raise(_rate_limit_key(request, user))
    except RateLimitError as exc:
        retry_after = str(int(math.ceil(exc.retry_after)))
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": retry_after},
            content=_error_payload("rate_limited", str(exc)),
        )

    driver_key = str(
        payload.get("driver") or payload.get("language") or ""
    ).strip()
    if not driver_key:
        return JSONResponse(
            status_code=400,
            content=_error_payload(
                "validation_error",
                "Chybí pole 'language' nebo 'driver'.",
            ),
        )

    raw_tests = payload.get("tests") or []
    if not isinstance(raw_tests, list):
        return JSONResponse(
            status_code=400,
            content=_error_payload(
                "validation_error",
                "Pole 'tests' musí být seznam.",
            ),
        )

    try:
        parsed_tests: list[TestCase] = []
        for item in raw_tests:
            if not isinstance(item, dict):
                raise ValidationError("Každý test musí být objekt.")
            parsed_tests.append(
                TestCase(
                    name=str(item.get("name", "")),
                    code=str(item.get("code", "")),
                    expected=item.get("expected"),
                    tolerance=item.get("tolerance", 1e-4),
                    hidden=bool(item.get("hidden", False)),
                )
            )
        exec_req = ExecutionRequest(
            language=str(payload.get("language") or driver_key),
            code=payload.get("code", ""),
            tests=parsed_tests,
            timeout_sec=payload.get("timeout_sec", 5.0),
            theme=str(payload.get("theme") or "dark"),
            dataset=payload.get("dataset"),
            expected_table=(
                payload.get("expected_table")
                if isinstance(payload.get("expected_table"), dict)
                else None
            ),
        )
        driver = default_registry.get(driver_key)
    except DriverNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content=_error_payload("error", str(exc)),
        )
    except ValidationError as exc:
        return JSONResponse(
            status_code=400,
            content=_error_payload("validation_error", str(exc)),
        )

    result = await driver.run_guarded(exec_req)
    status_code = 503 if result.status == "unavailable" else 200
    return JSONResponse(status_code=status_code, content=result.to_api_dict())
