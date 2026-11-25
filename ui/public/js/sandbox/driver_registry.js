/**
 * Unified client-side polyglot sandbox LanguageDriver registry.
 * Dispatches Tier 1 browser WASM/JS workers (R, Python, PG SQL, NoSQL)
 * with hard worker-termination timeouts and optional Tier 2 server fallback.
 *
 * Strict CSP / Trusted Types compliant: safe DOM & structured JSON only.
 */

const MAX_CODE_BYTES = 16 * 1024;
const MIN_TIMEOUT_MS = 1000;
const MAX_TIMEOUT_MS = 15000;
const DEFAULT_TIMEOUT_MS = 5000;
const PNG_MAGIC = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

export const WORKER_SCRIPT_URLS = Object.freeze({
  r: "/static/js/sandbox/webr_worker.js",
  python: "/static/js/sandbox/pyodide_worker.js",
  sql_pg: "/static/js/sandbox/pglite_worker.js",
  nosql: "/static/js/sandbox/nosql_worker.js",
});

function utf8ByteLength(text) {
  return new TextEncoder().encode(text).length;
}

function clampTimeout(timeoutMs) {
  const n = Number(timeoutMs);
  if (!Number.isFinite(n)) {
    return DEFAULT_TIMEOUT_MS;
  }
  return Math.max(MIN_TIMEOUT_MS, Math.min(MAX_TIMEOUT_MS, Math.round(n)));
}

export function isValidPngBytes(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 24) {
    return false;
  }
  for (let i = 0; i < PNG_MAGIC.length; i += 1) {
    if (bytes[i] !== PNG_MAGIC[i]) {
      return false;
    }
  }
  // Verify IHDR chunk tag right after length field.
  return (
    bytes[12] === 0x49 &&
    bytes[13] === 0x48 &&
    bytes[14] === 0x44 &&
    bytes[15] === 0x52
  );
}

function decodeBase64Png(b64) {
  if (typeof b64 !== "string" || !b64) {
    return null;
  }
  try {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) {
      out[i] = bin.charCodeAt(i);
    }
    return isValidPngBytes(out) ? out : null;
  } catch {
    return null;
  }
}

function getCsrfToken() {
  if (typeof document === "undefined") {
    return "";
  }
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? String(meta.getAttribute("content") || "").trim() : "";
}

function normalizeResult(raw, driverId, elapsedMs) {
  const plots = [];
  const rawPlots = Array.isArray(raw && raw.plots) ? raw.plots : [];
  for (const item of rawPlots.slice(0, 8)) {
    const bytes =
      item instanceof Uint8Array
        ? item
        : item instanceof ArrayBuffer
          ? new Uint8Array(item)
          : item && item.bytes instanceof Uint8Array
            ? item.bytes
            : item && item.bytes instanceof ArrayBuffer
              ? new Uint8Array(item.bytes)
              : null;
    if (bytes && isValidPngBytes(bytes)) {
      plots.push({
        bytes,
        caption: typeof item.caption === "string" ? item.caption : "",
      });
    }
  }

  const rawB64 = Array.isArray(raw && raw.plots_b64) ? raw.plots_b64 : [];
  for (let i = 0; i < rawB64.length && plots.length < 8; i += 1) {
    const decoded = decodeBase64Png(rawB64[i]);
    if (decoded) {
      plots.push({ bytes: decoded, caption: `Graf ${plots.length + 1}` });
    }
  }

  return {
    ok: Boolean(raw && raw.ok),
    status: String((raw && raw.status) || (raw && raw.ok ? "ok" : "error")),
    stdout: String((raw && raw.stdout) || ""),
    stderr: String((raw && raw.stderr) || ""),
    tests: Array.isArray(raw && raw.tests) ? raw.tests : [],
    table_diff: (raw && raw.table_diff) || null,
    plots,
    elapsed_ms: Number.isFinite(raw && raw.elapsed_ms)
      ? Math.max(0, Math.round(raw.elapsed_ms))
      : Math.max(0, Math.round(elapsedMs)),
    driver: driverId,
    fallbackRecommended: Boolean(raw && raw.fallbackRecommended),
  };
}

/**
 * Tier 1 browser Web Worker driver with hard termination timeout on main thread.
 */
export class WorkerLanguageDriver {
  constructor({ id, language, name, workerUrl, initConfig = {} }) {
    this.id = id;
    this.language = language;
    this.name = name;
    this.tier = "tier1_wasm";
    this.workerUrl = workerUrl;
    this.initConfig = initConfig;
    this._worker = null;
    this._reqSeq = 0;
  }

  _ensureWorker() {
    if (!this._worker) {
      this._worker = new Worker(this.workerUrl, { type: "module" });
    }
    return this._worker;
  }

  dispose() {
    if (this._worker) {
      this._worker.terminate();
      this._worker = null;
    }
  }

  run(payload) {
    const timeoutMs = clampTimeout(payload.timeoutMs);
    const started = performance.now();
    const requestId = ++this._reqSeq;

    return new Promise((resolve) => {
      let settled = false;
      let worker;

      try {
        worker = this._ensureWorker();
      } catch (err) {
        resolve(
          normalizeResult(
            {
              ok: false,
              status: "unavailable",
              stderr: `Nepodařilo se spustit Web Worker: ${String(err && err.message ? err.message : err)}`,
              fallbackRecommended: true,
            },
            this.id,
            performance.now() - started,
          ),
        );
        return;
      }

      const cleanup = () => {
        clearTimeout(timer);
        if (worker) {
          worker.removeEventListener("message", onMessage);
          worker.removeEventListener("error", onError);
        }
      };

      const finish = (resultObj) => {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        resolve(normalizeResult(resultObj, this.id, performance.now() - started));
      };

      const timer = setTimeout(() => {
        if (settled) {
          return;
        }
        // Hard isolation timeout: kill the worker completely so infinite loops stop.
        this.dispose();
        finish({
          ok: false,
          status: "timeout",
          stderr: `Překročen časový limit běhu (${(timeoutMs / 1000).toFixed(1)} s). Worker byl ukončen.`,
        });
      }, timeoutMs);

      const onMessage = (event) => {
        const msg = event.data;
        if (!msg || msg.requestId !== requestId) {
          return;
        }
        if (msg.type === "status") {
          if (typeof payload.onStatus === "function") {
            payload.onStatus(String(msg.message || ""));
          }
          return;
        }
        if (msg.type === "result") {
          finish(msg.result);
        }
      };

      const onError = (err) => {
        this.dispose();
        finish({
          ok: false,
          status: "error",
          stderr: `Chyba workeru: ${String(err && err.message ? err.message : "neznámá chyba")}`,
          fallbackRecommended: true,
        });
      };

      worker.addEventListener("message", onMessage);
      worker.addEventListener("error", onError);

      worker.postMessage({
        type: payload.action || "run",
        requestId,
        config: this.initConfig,
        payload: {
          language: this.language,
          code: String(payload.code || ""),
          visibleTests: Array.isArray(payload.visibleTests)
            ? payload.visibleTests
            : [],
          hiddenTests: Array.isArray(payload.hiddenTests)
            ? payload.hiddenTests
            : [],
          theme: payload.theme === "light" ? "light" : "dark",
          dataset: payload.dataset ?? null,
          seedSql: typeof payload.seedSql === "string" ? payload.seedSql : "",
          seedDocs: payload.seedDocs ?? null,
          expectedTable: payload.expectedTable ?? null,
          orderSensitive: Boolean(payload.orderSensitive),
        },
      });
    });
  }

  async introspectSchema(seedPayload) {
    const res = await this.run({
      action: "schema",
      code: "",
      seedSql: seedPayload && seedPayload.seedSql ? seedPayload.seedSql : "",
      seedDocs: seedPayload && seedPayload.seedDocs ? seedPayload.seedDocs : null,
      timeoutMs: 5000,
    });
    return res;
  }
}

/**
 * Tier 2 server-side Docker fallback driver via POST /api/sandbox/run.
 */
export class ServerLanguageDriver {
  constructor({ id, language, name, endpoint = "/api/sandbox/run" }) {
    this.id = id;
    this.language = language;
    this.name = name;
    this.tier = "tier2_docker";
    this.endpoint = endpoint;
  }

  dispose() {
    // Stateless HTTP driver.
  }

  async run(payload) {
    const started = performance.now();
    const timeoutMs = clampTimeout(payload.timeoutMs);
    const allTests = [
      ...(Array.isArray(payload.visibleTests)
        ? payload.visibleTests.map((t) => ({ ...t, hidden: false }))
        : []),
      ...(Array.isArray(payload.hiddenTests)
        ? payload.hiddenTests.map((t) => ({ ...t, hidden: true }))
        : []),
    ];

    const controller = new AbortController();
    const abortTimer = setTimeout(() => controller.abort(), timeoutMs + 2500);

    try {
      const headers = { "Content-Type": "application/json" };
      const csrf = getCsrfToken();
      if (csrf) {
        headers["X-CSRF-Token"] = csrf;
      }

      const resp = await fetch(this.endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers,
        signal: controller.signal,
        body: JSON.stringify({
          driver: this.id,
          language: this.language,
          code: String(payload.code || ""),
          tests: allTests,
          timeout_sec: Math.max(1, Math.min(10, timeoutMs / 1000)),
          theme: payload.theme === "light" ? "light" : "dark",
          dataset: payload.dataset ?? null,
          expected_table: payload.expectedTable ?? null,
        }),
      });

      const data = await resp.json().catch(() => null);
      if (!data) {
        return normalizeResult(
          {
            ok: false,
            status: resp.status === 503 ? "unavailable" : "error",
            stderr: `Server sandbox vrátil HTTP ${resp.status}.`,
          },
          this.id,
          performance.now() - started,
        );
      }
      return normalizeResult(data, this.id, performance.now() - started);
    } catch (err) {
      const aborted = err && err.name === "AbortError";
      return normalizeResult(
        {
          ok: false,
          status: aborted ? "timeout" : "unavailable",
          stderr: aborted
            ? "Vypršel časový limit spojení se serverovým sandboxem."
            : "Serverový sandbox není dostupný.",
        },
        this.id,
        performance.now() - started,
      );
    } finally {
      clearTimeout(abortTimer);
    }
  }
}

/**
 * Pluggable registry coordinating Tier 1 workers and Tier 2 server drivers.
 */
export class SandboxDriverRegistry {
  constructor() {
    this._primaryByLang = new Map();
    this._fallbackByLang = new Map();
    this._byId = new Map();
  }

  register(driver, { fallback = false } = {}) {
    const lang = String(driver.language || "").toLowerCase();
    const id = String(driver.id || "").toLowerCase();
    if (!lang || !id) {
      throw new Error("Sandbox driver must define non-empty id and language.");
    }
    this._byId.set(id, driver);
    if (fallback) {
      this._fallbackByLang.set(lang, driver);
    } else {
      this._primaryByLang.set(lang, driver);
    }
  }

  get(languageOrId, { tier = "auto" } = {}) {
    const key = String(languageOrId || "").toLowerCase();
    if (tier === "server" || tier === "tier2_docker") {
      if (this._fallbackByLang.has(key)) {
        return this._fallbackByLang.get(key);
      }
      const direct = this._byId.get(key);
      if (direct && direct.tier === "tier2_docker") {
        return direct;
      }
      return null;
    }
    if (this._primaryByLang.has(key)) {
      return this._primaryByLang.get(key);
    }
    if (this._byId.has(key)) {
      return this._byId.get(key);
    }
    return this._fallbackByLang.get(key) || null;
  }

  listLanguages() {
    const langs = new Set([
      ...this._primaryByLang.keys(),
      ...this._fallbackByLang.keys(),
    ]);
    return Array.from(langs);
  }

  async run(request) {
    const code = String((request && request.code) || "");
    if (!code.trim() && request.action !== "schema") {
      return normalizeResult(
        {
          ok: false,
          status: "validation_error",
          stderr: "Kód nesmí být prázdný.",
        },
        "none",
        0,
      );
    }
    if (utf8ByteLength(code) > MAX_CODE_BYTES) {
      return normalizeResult(
        {
          ok: false,
          status: "validation_error",
          stderr: `Kód překračuje limit ${MAX_CODE_BYTES / 1024} KB.`,
        },
        "none",
        0,
      );
    }

    const lang = String(request.language || "").toLowerCase();
    const preferredTier = request.tier || "auto";
    const driver = this.get(lang, { tier: preferredTier });
    if (!driver) {
      return normalizeResult(
        {
          ok: false,
          status: "unavailable",
          stderr: `Pro jazyk '${lang}' není registrován žádný sandbox driver.`,
        },
        "none",
        0,
      );
    }

    const result = await driver.run(request);
    if (
      !result.ok &&
      result.fallbackRecommended &&
      preferredTier === "auto" &&
      request.allowServerFallback
    ) {
      const serverFallback = this._fallbackByLang.get(lang);
      if (serverFallback && serverFallback !== driver) {
        return serverFallback.run(request);
      }
    }
    return result;
  }

  disposeAll() {
    for (const driver of this._byId.values()) {
      driver.dispose();
    }
  }
}

export function createDefaultDriverRegistry() {
  const reg = new SandboxDriverRegistry();

  reg.register(
    new WorkerLanguageDriver({
      id: "webr",
      language: "r",
      name: "R · WebR Sandbox (lokální WASM)",
      workerUrl: WORKER_SCRIPT_URLS.r,
    }),
  );
  reg.register(
    new ServerLanguageDriver({
      id: "r_docker",
      language: "r",
      name: "R · Serverový Docker Sandbox",
    }),
    { fallback: true },
  );

  reg.register(
    new WorkerLanguageDriver({
      id: "pyodide",
      language: "python",
      name: "Python · Pyodide Sandbox (lokální WASM)",
      workerUrl: WORKER_SCRIPT_URLS.python,
    }),
  );

  reg.register(
    new WorkerLanguageDriver({
      id: "pglite",
      language: "sql_pg",
      name: "PostgreSQL · PGlite Sandbox (lokální WASM)",
      workerUrl: WORKER_SCRIPT_URLS.sql_pg,
    }),
  );

  reg.register(
    new WorkerLanguageDriver({
      id: "nosql_mem",
      language: "nosql",
      name: "NoSQL · In-Memory Pipeline Worker",
      workerUrl: WORKER_SCRIPT_URLS.nosql,
    }),
  );

  reg.register(
    new ServerLanguageDriver({
      id: "csharp_dotnet",
      language: "csharp",
      name: "C# .NET · Docker xUnit Sandbox",
    }),
  );

  return reg;
}

export const defaultSandboxRegistry = createDefaultDriverRegistry();
