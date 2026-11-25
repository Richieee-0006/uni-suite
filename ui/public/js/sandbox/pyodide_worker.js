/**
 * Phase 4 ready Tier 1 Pyodide WASM Worker for Python (KI/APR).
 * - Lazily loads Pyodide from same-origin /static/vendor/pyodide/pyodide.mjs.
 * - Executes student Python code inside an isolated namespace capturing stdout/stderr.
 * - Evaluates visible_tests & hidden_tests with numeric tolerance.
 * - Extracts tabular outputs for Expected vs. Actual Result Diff.
 */

const DEFAULT_PYODIDE_URL = "/static/vendor/pyodide/pyodide.mjs";
const MAX_STDOUT_BYTES = 32 * 1024;

let pyodideInstance = null;
let pyodideInitPromise = null;

function truncateUtf8(text, maxBytes = MAX_STDOUT_BYTES) {
  const encoder = new TextEncoder();
  const bytes = encoder.encode(String(text || ""));
  if (bytes.byteLength <= maxBytes) {
    return String(text || "");
  }
  const suffix = "\n...[zkráceno na limit 32 KB]";
  const suffixBytes = encoder.encode(suffix);
  const slice = bytes.slice(0, Math.max(0, maxBytes - suffixBytes.byteLength));
  return new TextDecoder("utf-8", { fatal: false }).decode(slice) + suffix;
}

async function ensurePyodide(config, requestId) {
  if (pyodideInstance) {
    return pyodideInstance;
  }
  if (!pyodideInitPromise) {
    pyodideInitPromise = (async () => {
      const moduleUrl =
        (config && typeof config.moduleUrl === "string" && config.moduleUrl) ||
        DEFAULT_PYODIDE_URL;
      self.postMessage({
        type: "status",
        requestId,
        message: "Inicializuji lokální Python (Pyodide WASM)…",
      });
      const mod = await import(moduleUrl);
      const loadPyodide = mod.loadPyodide || (mod.default && mod.default.loadPyodide);
      if (typeof loadPyodide !== "function") {
        throw new Error("Pyodide modul neexportuje funkci loadPyodide.");
      }
      const opts = {};
      if (config && typeof config.indexURL === "string" && config.indexURL) {
        opts.indexURL = config.indexURL;
      }
      const instance = await loadPyodide(opts);
      pyodideInstance = instance;
      return instance;
    })().catch((err) => {
      pyodideInitPromise = null;
      throw err;
    });
  }
  return pyodideInitPromise;
}

const PY_HARNESS = `
import contextlib
import io
import json
import math
import time
import traceback

def _uni_cells_eq(a, b, tol=1e-4):
    if a is None and b is None:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol)
    return str(a) == str(b)

def _uni_run_payload(payload_json):
    data = json.loads(payload_json)
    code = str(data.get("code") or "")
    tests = list(data.get("tests") or [])
    expected_table = data.get("expectedTable")

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    ns = {"__name__": "__main__"}
    run_ok = True

    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        try:
            compiled = compile(code, "<solution.py>", "exec")
            exec(compiled, ns, ns)
        except BaseException:
            run_ok = False
            traceback.print_exc(file=err_buf)

    test_results = []
    if run_ok:
        for tc in tests:
            t0 = time.perf_counter()
            hidden = bool(tc.get("hidden", False))
            expected = tc.get("expected")
            tol = float(tc.get("tolerance", 1e-4) or 1e-4)
            res = {
                "name": str(tc.get("name") or "Test"),
                "passed": False,
                "actual": None,
                "expected": None if hidden else expected,
                "message": "",
                "elapsed_ms": 0,
                "hidden": hidden,
            }
            try:
                tcode = str(tc.get("code") or "")
                try:
                    val = eval(compile(tcode, "<test>", "eval"), ns, ns)
                except SyntaxError:
                    exec(compile(tcode, "<test>", "exec"), ns, ns)
                    val = True
                if expected is None and isinstance(val, bool):
                    passed = val is True
                elif isinstance(val, (int, float)) and isinstance(expected, (int, float)):
                    passed = math.isfinite(val) and math.isclose(
                        float(val), float(expected), rel_tol=0.0, abs_tol=tol
                    )
                else:
                    passed = val == expected
                res["passed"] = bool(passed)
                res["actual"] = (
                    ("ověřeno" if passed else "neshoda") if hidden else val
                )
            except BaseException as exc:
                res["passed"] = False
                res["message"] = str(exc)
            res["elapsed_ms"] = int(round((time.perf_counter() - t0) * 1000))
            test_results.append(res)

    table_diff = None
    cand = ns.get("result_table")
    if isinstance(cand, list) and cand and isinstance(cand[0], dict):
        cols = list(cand[0].keys())
        act_rows = [[row.get(c) for c in cols] for row in cand[:100]]
        exp_rows = (
            expected_table.get("rows", act_rows)
            if isinstance(expected_table, dict)
            else act_rows
        )
        max_len = max(len(exp_rows), len(act_rows))
        row_matches = []
        for idx in range(max_len):
            if idx >= len(exp_rows) or idx >= len(act_rows):
                row_matches.append(False)
            elif len(exp_rows[idx]) != len(act_rows[idx]):
                row_matches.append(False)
            else:
                row_matches.append(
                    all(
                        _uni_cells_eq(a, e)
                        for a, e in zip(act_rows[idx], exp_rows[idx])
                    )
                )
        table_diff = {
            "columns": [str(c) for c in cols],
            "expected_rows": exp_rows,
            "actual_rows": act_rows,
            "row_matches": row_matches,
            "matched": len(exp_rows) == len(act_rows) and all(row_matches),
        }

    all_pass = all(t["passed"] for t in test_results) if test_results else True
    return json.dumps(
        {
            "ok": run_ok and all_pass,
            "status": "ok" if run_ok else "error",
            "stdout": out_buf.getvalue(),
            "stderr": err_buf.getvalue(),
            "tests": test_results,
            "table_diff": table_diff,
        },
        default=str,
    )
`;

async function handleRun(requestId, config, payload) {
  const started = performance.now();
  let pyodide;
  try {
    pyodide = await ensurePyodide(config, requestId);
  } catch (err) {
    self.postMessage({
      type: "result",
      requestId,
      result: {
        ok: false,
        status: "unavailable",
        stdout: "",
        stderr:
          "Pyodide WASM runtime není nainstalován: " +
          String(err && err.message ? err.message : err),
        tests: [],
        table_diff: null,
        plots: [],
        elapsed_ms: Math.round(performance.now() - started),
        fallbackRecommended: true,
      },
    });
    return;
  }

  try {
    await pyodide.runPythonAsync(PY_HARNESS);
    const allTests = [
      ...(Array.isArray(payload.visibleTests)
        ? payload.visibleTests.map((t) => ({ ...t, hidden: false }))
        : []),
      ...(Array.isArray(payload.hiddenTests)
        ? payload.hiddenTests.map((t) => ({ ...t, hidden: true }))
        : []),
    ];
    pyodide.globals.set(
      "_uni_payload_str",
      JSON.stringify({
        code: String(payload.code || ""),
        tests: allTests,
        expectedTable: payload.expectedTable || null,
      }),
    );
    const outStr = await pyodide.runPythonAsync(
      "_uni_run_payload(_uni_payload_str)",
    );
    const parsed = JSON.parse(String(outStr));

    self.postMessage({
      type: "result",
      requestId,
      result: {
        ok: Boolean(parsed.ok),
        status: String(parsed.status || "ok"),
        stdout: truncateUtf8(parsed.stdout || ""),
        stderr: truncateUtf8(parsed.stderr || "", 16 * 1024),
        tests: Array.isArray(parsed.tests) ? parsed.tests : [],
        table_diff: parsed.table_diff || null,
        plots: [],
        elapsed_ms: Math.round(performance.now() - started),
      },
    });
  } catch (err) {
    self.postMessage({
      type: "result",
      requestId,
      result: {
        ok: false,
        status: "error",
        stdout: "",
        stderr: truncateUtf8(String(err && err.message ? err.message : err), 16 * 1024),
        tests: [],
        table_diff: null,
        plots: [],
        elapsed_ms: Math.round(performance.now() - started),
      },
    });
  }
}

if (typeof self !== "undefined" && typeof self.postMessage === "function") {
  self.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || msg.type !== "run") {
      return;
    }
    handleRun(msg.requestId, msg.config || {}, msg.payload || {});
  });
}
