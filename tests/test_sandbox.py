"""Unit, security, concurrency, Docker flag, API & JS worker tests for Sandbox."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from webapp.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from webapp.routers import sandbox as sandbox_router_mod
from webapp.sandbox.base import (
    EXECUTION_SEMAPHORE,
    MAX_CODE_BYTES,
    MAX_PLOT_BYTES,
    MAX_PLOTS,
    MAX_STDOUT_BYTES,
    PNG_MAGIC,
    DriverNotFoundError,
    ExecutionRequest,
    ExecutionResult,
    LanguageDriver,
    PlotArtifact,
    RateLimitError,
    SlidingWindowRateLimiter,
    TestResult,
    ValidationError,
    compute_table_diff,
    truncate_utf8,
    validate_png_bytes,
)
from webapp.sandbox.drivers.csharp_dotnet import CSharpDotnetDriver
from webapp.sandbox.drivers.r_docker import (
    RESULT_SENTINEL,
    RDockerDriver,
    _stripped_host_env,
)
from webapp.sandbox.registry import DriverRegistry, default_registry
from webapp.sessions import create_trial_session

REPO_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_JS_DIR = REPO_ROOT / "ui" / "public" / "js" / "sandbox"


def _make_minimal_png(width: int = 1, height: int = 1) -> bytes:
    """Construct a valid minimal RGBA PNG byte buffer with real CRCs."""

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    idat = zlib.compress(raw_scanlines)
    return (
        PNG_MAGIC
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# 1. Size limits, stdout truncation, timeout & PNG magic-byte security
# ---------------------------------------------------------------------------


def test_code_size_16kb_limit() -> None:
    ok_req = ExecutionRequest(language="r", code="x" * MAX_CODE_BYTES)
    assert len(ok_req.code.encode("utf-8")) == MAX_CODE_BYTES

    with pytest.raises(ValidationError, match="16384"):
        ExecutionRequest(language="r", code="x" * (MAX_CODE_BYTES + 1))

    with pytest.raises(ValidationError):
        ExecutionRequest(language="r", code="")


def test_timeout_bounds_validation() -> None:
    req_min = ExecutionRequest(language="r", code="1+1", timeout_sec=1.0)
    req_max = ExecutionRequest(language="r", code="1+1", timeout_sec=10.0)
    assert req_min.timeout_sec == 1.0
    assert req_max.timeout_sec == 10.0

    for bad_timeout in (0.0, 0.5, -2.0, 10.1, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            ExecutionRequest(language="r", code="1+1", timeout_sec=bad_timeout)


def test_stdout_32kb_truncation_preserves_utf8() -> None:
    exact = "a" * MAX_STDOUT_BYTES
    assert truncate_utf8(exact, MAX_STDOUT_BYTES) == exact

    # Multi-byte Czech text must truncate strictly <= 32 KB without invalid UTF-8.
    czech = "Příliš žluťoučký kůň úpěl ďábelské ódy.\n" * 1500
    assert len(czech.encode("utf-8")) > MAX_STDOUT_BYTES
    truncated = truncate_utf8(czech, MAX_STDOUT_BYTES)
    encoded = truncated.encode("utf-8")
    assert len(encoded) <= MAX_STDOUT_BYTES
    assert encoded.decode("utf-8") == truncated

    res = ExecutionResult(ok=True, status="ok", stdout=czech)
    assert len(res.stdout.encode("utf-8")) <= MAX_STDOUT_BYTES


def test_png_magic_bytes_accepts_real_png_and_rejects_forged_payloads() -> None:
    valid_png = _make_minimal_png(4, 3)
    width, height = validate_png_bytes(valid_png)
    assert (width, height) == (4, 3)

    artifact = PlotArtifact(data=valid_png, caption="Histogram")
    assert artifact.width == 4
    assert artifact.height == 3
    assert base64.b64decode(artifact.to_base64()) == valid_png

    # Reject raw SVG, HTML, empty bytes, and PNG-magic + forged SVG polyglot.
    forged_payloads = [
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
        b"<html><body><script>alert(1)</script></body></html>",
        b"",
        b"short",
        PNG_MAGIC + b"<svg onload=alert(1)></svg>",
        PNG_MAGIC + b"\x00\x00\x00\rFAKE" + b"\x00" * 16,
    ]
    for bad in forged_payloads:
        with pytest.raises(ValidationError):
            PlotArtifact(data=bad)

    # Reject oversized PNG > 512 KB.
    oversized = valid_png + b"\x00" * (MAX_PLOT_BYTES + 1)
    with pytest.raises(ValidationError):
        PlotArtifact(data=oversized)


def test_execution_result_caps_plots_at_eight() -> None:
    valid_png = _make_minimal_png(2, 2)
    plots = [PlotArtifact(data=valid_png, caption=f"P{i}") for i in range(12)]
    res = ExecutionResult(ok=True, status="ok", plots=plots)
    assert len(res.plots) == MAX_PLOTS
    assert len(res.to_api_dict()["plots_b64"]) == MAX_PLOTS


def test_r_harness_parser_drops_forged_svg_and_keeps_valid_png() -> None:
    driver = RDockerDriver()
    valid_b64 = base64.b64encode(_make_minimal_png(8, 6)).decode("ascii")
    forged_svg_b64 = base64.b64encode(b"<svg onload=alert(1)></svg>").decode("ascii")
    payload = {
        "ok": True,
        "stdout": "42",
        "stderr": "",
        "tests": [
            {
                "name": "Hidden test",
                "passed": True,
                "actual": 0.3333,
                "expected": 0.3333,
                "hidden": True,
                "elapsed_ms": 4,
            }
        ],
        "table": None,
        "plots_b64": [forged_svg_b64, valid_b64],
    }
    raw_stdout = f"noise\n{RESULT_SENTINEL}\n{json.dumps(payload)}\n"
    req = ExecutionRequest(language="r", code="plot(1:5)")
    res = driver._parse_harness_output(raw_stdout, "", req, elapsed_ms=25)
    assert res.ok is True
    assert len(res.plots) == 1
    api = res.to_api_dict()
    assert len(api["plots_b64"]) == 1
    assert api["tests"][0]["actual"] == "ověřeno"
    assert api["tests"][0]["expected"] is None


# ---------------------------------------------------------------------------
# 2. Sliding-window rate limiter (10/min) & Semaphore concurrency cap (= 2)
# ---------------------------------------------------------------------------


def test_sliding_window_rate_limiter_10_per_minute() -> None:
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(
        max_requests=10,
        window_seconds=60.0,
        clock=lambda: now[0],
    )

    for _ in range(10):
        limiter.check_or_raise("user:42")

    with pytest.raises(RateLimitError) as exc_info:
        limiter.check_or_raise("user:42")
    assert exc_info.value.retry_after > 0

    # Other users have an independent quota.
    limiter.check_or_raise("user:99")

    # Advancing time past the 60s window frees slots cleanly.
    now[0] += 60.5
    limiter.check_or_raise("user:42")


def test_global_semaphore_concurrency_cap_of_two() -> None:
    assert EXECUTION_SEMAPHORE._value == 2

    class SlowMockDriver(LanguageDriver):
        driver_id = "slow_mock"
        language = "mock"
        display_name = "Slow Mock"

        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        def is_available(self) -> tuple[bool, str]:
            return True, ""

        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.05)
            finally:
                self.active -= 1
            return ExecutionResult(ok=True, status="ok", stdout="done")

    async def _run_five() -> int:
        driver = SlowMockDriver()
        sem = asyncio.Semaphore(2)
        req = ExecutionRequest(language="mock", code="1")
        results = await asyncio.gather(
            *(driver.run_guarded(req, semaphore=sem) for _ in range(5))
        )
        assert all(r.ok for r in results)
        return driver.peak

    peak_concurrent = asyncio.run(_run_five())
    assert peak_concurrent == 2


# ---------------------------------------------------------------------------
# 3. Docker security flags, stripped env, graceful unavailable & registry
# ---------------------------------------------------------------------------


def test_r_docker_security_flags_and_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = RDockerDriver()
    cmd = driver.build_docker_command("uni-sbx-test", docker_bin="/usr/bin/docker")

    assert cmd[:3] == ["/usr/bin/docker", "run", "--rm"]
    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt" in cmd
    assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    assert "--user" in cmd
    assert cmd[cmd.index("--user") + 1] == "65534:65534"
    assert "--memory=256m" in cmd
    assert "--cpus=0.5" in cmd
    assert "--pids-limit=64" in cmd
    assert "--tmpfs" in cmd
    assert cmd[cmd.index("--tmpfs") + 1].startswith("/tmp:")

    # Host secrets must never leak into docker subprocess environment.
    monkeypatch.setenv("APP_SECRET", "super-secret-value")
    monkeypatch.setenv("HASHED_PASSWORD", "$argon2id$secret")
    env = _stripped_host_env()
    assert "APP_SECRET" not in env
    assert "HASHED_PASSWORD" not in env
    assert "PATH" in env

    # Disabled by default when SANDBOX_DOCKER_ENABLED != 1.
    monkeypatch.delenv("SANDBOX_DOCKER_ENABLED", raising=False)
    avail, reason = driver.is_available()
    assert avail is False
    assert "SANDBOX_DOCKER_ENABLED" in reason

    req = ExecutionRequest(language="r", code="1 + 1")
    res = asyncio.run(driver.run_guarded(req))
    assert res.ok is False
    assert res.status == "unavailable"

    with pytest.raises(ValidationError):
        RDockerDriver(image="r-base; rm -rf /")


def test_csharp_docker_security_flags_and_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = CSharpDotnetDriver()
    cmd = driver.build_docker_command("uni-sbx-cs-test", docker_bin="/usr/bin/docker")

    assert cmd[:3] == ["/usr/bin/docker", "run", "--rm"]
    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt" in cmd
    assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    assert "--user" in cmd
    assert cmd[cmd.index("--user") + 1] == "65534:65534"
    assert "--memory=512m" in cmd
    assert "--cpus=0.5" in cmd
    assert "--pids-limit=64" in cmd
    assert "uni-sandbox-csharp:latest" in cmd

    monkeypatch.setenv("SANDBOX_DOCKER_ENABLED", "0")
    avail, _ = driver.is_available()
    assert avail is False


def test_driver_registry_pluggable_and_table_diff() -> None:
    assert default_registry.get("r_docker").driver_id == "r_docker"
    assert default_registry.get("r").driver_id == "r_docker"
    assert default_registry.get("csharp_dotnet").driver_id == "csharp_dotnet"
    assert default_registry.get("csharp").driver_id == "csharp_dotnet"

    reg = DriverRegistry()
    with pytest.raises(DriverNotFoundError):
        reg.get("unknown_lang")

    diff = compute_table_diff(
        columns=["x", "y"],
        expected_rows=[[1, 2.0], [2, 4.0]],
        actual_rows=[[1, 2.00001], [2, 9.0]],
        tolerance=1e-3,
    )
    assert diff.matched is False
    assert diff.row_matches == [True, False]


# ---------------------------------------------------------------------------
# 4. FastAPI Router (/api/sandbox/drivers & /api/sandbox/run)
# ---------------------------------------------------------------------------


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(sandbox_router_mod.router)

    @app.get("/_test/trial-login")
    async def _issue_trial(request: Request) -> JSONResponse:
        resp = JSONResponse({"ok": True})
        create_trial_session(resp, request)
        return resp

    return app


def test_sandbox_router_auth_csrf_limits_and_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_SECRET", "sandbox-test-secret-key-at-least-32-chars!!")
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("SANDBOX_DOCKER_ENABLED", "0")
    sandbox_router_mod.sandbox_rate_limiter.reset()

    app = _build_test_app()
    client = TestClient(app)

    # 1. GET /api/sandbox/drivers exposes Tier 1 + Tier 2 + limits.
    drv_resp = client.get("/api/sandbox/drivers")
    assert drv_resp.status_code == 200
    drv_data = drv_resp.json()
    assert {d["language"] for d in drv_data["tier1"]} == {
        "r",
        "python",
        "sql_pg",
        "nosql",
    }
    assert {d["id"] for d in drv_data["tier2"]} >= {"r_docker", "csharp_dotnet"}
    assert drv_data["limits"]["max_code_bytes"] == MAX_CODE_BYTES

    # 2. CSRF is enforced even with User-Agent: testclient when TESTING=0.
    csrf_fail = client.post(
        "/api/sandbox/run",
        json={"language": "r", "code": "1 + 1"},
        headers={"User-Agent": "testclient"},
    )
    assert csrf_fail.status_code == 403

    # Attach valid double-submit CSRF cookie + header.
    csrf_tok = "A" * 40
    client.cookies.set(CSRF_COOKIE_NAME, csrf_tok)
    csrf_headers = {CSRF_HEADER_NAME: csrf_tok}

    # 3. Unauthenticated request is rejected with 401.
    unauth = client.post(
        "/api/sandbox/run",
        json={"language": "r", "code": "1 + 1"},
        headers=csrf_headers,
    )
    assert unauth.status_code == 401

    # Obtain valid Trial cookie.
    assert client.get("/_test/trial-login").status_code == 200

    # 4. Oversized >16 KB code is rejected with 400 validation_error.
    too_big = client.post(
        "/api/sandbox/run",
        json={"language": "r", "code": "x" * (MAX_CODE_BYTES + 10)},
        headers=csrf_headers,
    )
    assert too_big.status_code == 400
    assert too_big.json()["status"] == "validation_error"

    # 5. Disabled Docker gracefully reports 503 unavailable with safe JSON shape.
    unavail = client.post(
        "/api/sandbox/run",
        json={"language": "r", "code": "1 + 1"},
        headers=csrf_headers,
    )
    assert unavail.status_code == 503
    body = unavail.json()
    assert body["ok"] is False
    assert body["status"] == "unavailable"
    assert set(body.keys()) == {
        "ok",
        "status",
        "stdout",
        "stderr",
        "tests",
        "table_diff",
        "plots_b64",
        "elapsed_ms",
    }

    # 6. Mocked driver returns 200 with verified PNG plots & test results.
    class FakeOkDriver(LanguageDriver):
        driver_id = "fake_r"
        language = "fake_r"
        display_name = "Fake R"

        def is_available(self) -> tuple[bool, str]:
            return True, ""

        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                ok=True,
                status="ok",
                stdout="3.14159",
                tests=[
                    TestResult(
                        name="t1",
                        passed=True,
                        actual=3.14159,
                        expected=3.14,
                    )
                ],
                plots=[PlotArtifact(data=_make_minimal_png(2, 2), caption="Graf 1")],
            )

    default_registry.register(FakeOkDriver())
    try:
        ok_resp = client.post(
            "/api/sandbox/run",
            json={"driver": "fake_r", "language": "fake_r", "code": "pi"},
            headers=csrf_headers,
        )
        assert ok_resp.status_code == 200
        ok_data = ok_resp.json()
        assert ok_data["ok"] is True
        assert ok_data["stdout"] == "3.14159"
        assert len(ok_data["plots_b64"]) == 1
        assert base64.b64decode(ok_data["plots_b64"][0]).startswith(PNG_MAGIC)
    finally:
        default_registry.unregister("fake_r")

    # 7. Rate limit 10/min triggers 429 on 11th call (we already made 3 valid calls).
    for _ in range(7):
        r = client.post(
            "/api/sandbox/run",
            json={"language": "r", "code": "1+1"},
            headers=csrf_headers,
        )
        assert r.status_code == 503

    limited = client.post(
        "/api/sandbox/run",
        json={"language": "r", "code": "1+1"},
        headers=csrf_headers,
    )
    assert limited.status_code == 429
    assert limited.json()["status"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    sandbox_router_mod.sandbox_rate_limiter.reset()


# ---------------------------------------------------------------------------
# 5. Frontend JS Security Gate & NoSQL Worker functional check via Node
# ---------------------------------------------------------------------------


def test_frontend_sandbox_js_strict_csp_and_zero_innerhtml() -> None:
    js_files = sorted(SANDBOX_JS_DIR.glob("*.js"))
    expected_names = {
        "code_editor.js",
        "driver_registry.js",
        "nosql_worker.js",
        "pglite_worker.js",
        "plot_viewer.js",
        "pyodide_worker.js",
        "result_diff.js",
        "sandbox_page.js",
        "webr_worker.js",
    }
    assert {f.name for f in js_files} == expected_names

    forbidden_patterns = [
        (r"\binnerHTML\b", "innerHTML"),
        (r"\bouterHTML\b", "outerHTML"),
        (r"\binsertAdjacentHTML\b", "insertAdjacentHTML"),
        (r"\bdocument\.write\b", "document.write"),
        (r"\bnew\s+Function\b", "new Function"),
        (r"(?<!['\"])\.\bstyle\s*\.", "inline .style mutation"),
    ]
    for js_file in js_files:
        src = js_file.read_text(encoding="utf-8")
        # Ignore comments/Python/R embedded strings for eval check: verify no JS eval(
        for pattern, label in forbidden_patterns:
            assert not re.search(pattern, src), f"{js_file.name} contains {label}"
        # Check no top-level JS eval( outside R/Python worker string literals.
        if js_file.name not in ("webr_worker.js", "pyodide_worker.js"):
            assert not re.search(r"\beval\s*\(", src), f"{js_file.name} contains eval()"


def test_nosql_worker_pipeline_operators_via_node() -> None:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node not installed")

    worker_path = (SANDBOX_JS_DIR / "nosql_worker.js").as_uri()
    script = f"""
import {{ executeNoSqlTask, introspectCollections }} from {json.dumps(worker_path)};

const seedDocs = {{
  orders: [
    {{ _id: 1, customerId: 10, tags: ["math", "r"], amount: 120, status: "paid" }},
    {{ _id: 2, customerId: 10, tags: ["r"], amount: 80, status: "paid" }},
    {{ _id: 3, customerId: 20, tags: ["sql"], amount: 50, status: "cancelled" }},
    {{ _id: 4, customerId: 20, tags: ["math", "sql"], amount: 200, status: "paid" }}
  ],
  customers: [
    {{ _id: 10, name: "Eva" }},
    {{ _id: 20, name: "Petr" }}
  ]
}};

const pipeline = [
  {{ "$match": {{ "status": "paid", "amount": {{ "$gte": 80 }} }} }},
  {{ "$unwind": "$tags" }},
  {{
    "$group": {{
      "_id": "$tags",
      "total": {{ "$sum": "$amount" }},
      "avg": {{ "$avg": "$amount" }},
      "cnt": {{ "$count": {{}} }}
    }}
  }},
  {{ "$sort": {{ "total": -1, "_id": 1 }} }},
  {{ "$limit": 2 }},
  {{ "$project": {{ "_id": 0, "tag": "$_id", "total": 1, "cnt": 1 }} }}
];

const res1 = executeNoSqlTask({{
  code: JSON.stringify({{ collection: "orders", pipeline }}),
  seedDocs,
  visibleTests: [
    {{ name: "Count 2", code: "__COUNT__", expected: 2 }},
    {{ name: "Top tags", code: "field:tag", expected: ["math", "r"] }}
  ],
  hiddenTests: [
    {{ name: "Totals", code: "field:total", expected: [320, 200] }}
  ]
}});

const lookupRes = executeNoSqlTask({{
  code: JSON.stringify({{
    collection: "orders",
    pipeline: [
      {{ "$match": {{ "_id": 1 }} }},
      {{
        "$lookup": {{
          "from": "customers",
          "localField": "customerId",
          "foreignField": "_id",
          "as": "cust"
        }}
      }},
      {{ "$project": {{ "_id": 0, "cust": 1 }} }}
    ]
  }}),
  seedDocs
}});

const schema = introspectCollections(seedDocs);
console.log(JSON.stringify({{ res1, lookup: JSON.parse(lookupRes.stdout), schema }}));
"""
    proc = subprocess.run(  # noqa: S603
        [node_bin, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    out = json.loads(proc.stdout)
    assert out["res1"]["ok"] is True
    assert all(t["passed"] for t in out["res1"]["tests"])
    assert out["res1"]["tests"][-1]["actual"] == "ověřeno"
    assert out["lookup"][0]["cust"][0]["name"] == "Eva"
    assert {c["name"] for c in out["schema"]} == {"orders", "customers"}
