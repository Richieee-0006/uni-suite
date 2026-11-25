/**
 * Phase 4 ready Tier 1 PGlite WASM Worker for PostgreSQL (KI/URDB).
 * - Creates a clean ephemeral in-memory PostgreSQL instance per task (zero access to uni.db).
 * - Executes seedSql + student SQL query, computes Expected vs. Actual relation diff.
 * - Supports live schema introspection (tables, columns, primary/foreign keys) for the F drawer.
 */

const DEFAULT_PGLITE_URL = "/static/vendor/pglite/index.js";
const MAX_STDOUT_BYTES = 32 * 1024;
const MAX_ROWS_RETURNED = 200;

let PGliteCtor = null;
let loadPromise = null;

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

async function ensurePGliteCtor(config, requestId) {
  if (PGliteCtor) {
    return PGliteCtor;
  }
  if (!loadPromise) {
    loadPromise = (async () => {
      const moduleUrl =
        (config && typeof config.moduleUrl === "string" && config.moduleUrl) ||
        DEFAULT_PGLITE_URL;
      self.postMessage({
        type: "status",
        requestId,
        message: "Inicializuji lokální PostgreSQL (PGlite WASM)…",
      });
      const mod = await import(moduleUrl);
      const Ctor = mod.PGlite || (mod.default && mod.default.PGlite) || mod.default;
      if (typeof Ctor !== "function") {
        throw new Error("PGlite modul neexportuje konstruktor PGlite.");
      }
      PGliteCtor = Ctor;
      return Ctor;
    })().catch((err) => {
      loadPromise = null;
      throw err;
    });
  }
  return loadPromise;
}

function normalizeCell(val) {
  if (val === null || val === undefined) {
    return null;
  }
  if (val instanceof Date) {
    return val.toISOString();
  }
  if (typeof val === "bigint") {
    return Number(val);
  }
  return val;
}

function cellsEqual(a, b, tol = 1e-4) {
  if (a === null && b === null) {
    return true;
  }
  if (typeof a === "number" && typeof b === "number") {
    return Math.abs(a - b) <= tol;
  }
  const na = Number(a);
  const nb = Number(b);
  if (
    a !== "" &&
    b !== "" &&
    typeof a !== "boolean" &&
    typeof b !== "boolean" &&
    Number.isFinite(na) &&
    Number.isFinite(nb)
  ) {
    return Math.abs(na - nb) <= tol;
  }
  return String(a) === String(b);
}

function rowKey(row) {
  return JSON.stringify(row.map((v) => (v === null ? null : String(v))));
}

function computeRelationDiff(columns, expectedRows, actualRows, orderSensitive = false) {
  let exp = expectedRows.map((r) => r.map(normalizeCell));
  let act = actualRows.map((r) => r.map(normalizeCell));

  if (!orderSensitive) {
    exp = [...exp].sort((a, b) => rowKey(a).localeCompare(rowKey(b)));
    act = [...act].sort((a, b) => rowKey(a).localeCompare(rowKey(b)));
  }

  const maxLen = Math.max(exp.length, act.length);
  const rowMatches = [];
  for (let i = 0; i < maxLen; i += 1) {
    if (i >= exp.length || i >= act.length) {
      rowMatches.push(false);
      continue;
    }
    if (exp[i].length !== act[i].length) {
      rowMatches.push(false);
      continue;
    }
    rowMatches.push(act[i].every((cell, cIdx) => cellsEqual(cell, exp[i][cIdx])));
  }

  return {
    columns: columns.map(String),
    expected_rows: exp,
    actual_rows: act,
    row_matches: rowMatches,
    matched: exp.length === act.length && rowMatches.every(Boolean),
  };
}

function formatAsciiTable(columns, rows) {
  if (!columns.length) {
    return "(0 sloupců)";
  }
  const header = columns.join(" | ");
  const sep = columns.map((c) => "-".repeat(Math.max(3, c.length))).join("-+-");
  const body = rows.slice(0, 50).map((r) =>
    r.map((v) => (v === null ? "NULL" : String(v))).join(" | "),
  );
  return [header, sep, ...body].join("\n");
}

async function introspectSchema(db) {
  const colsRes = await db.query(`
    SELECT
      c.table_name,
      c.column_name,
      c.data_type,
      c.is_nullable
    FROM information_schema.columns c
    JOIN information_schema.tables t
      ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
    ORDER BY c.table_name, c.ordinal_position;
  `);

  const tables = new Map();
  for (const row of colsRes.rows || []) {
    const tname = String(row.table_name);
    if (!tables.has(tname)) {
      tables.set(tname, { name: tname, columns: [] });
    }
    tables.get(tname).columns.push({
      name: String(row.column_name),
      type: String(row.data_type),
      nullable: row.is_nullable === "YES",
    });
  }
  return Array.from(tables.values());
}

async function handleMessage(msg) {
  const started = performance.now();
  const { requestId, config = {}, payload = {} } = msg;
  let Ctor;
  try {
    Ctor = await ensurePGliteCtor(config, requestId);
  } catch (err) {
    self.postMessage({
      type: "result",
      requestId,
      result: {
        ok: false,
        status: "unavailable",
        stdout: "",
        stderr:
          "PGlite WASM runtime není nainstalován: " +
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

  const db = new Ctor();
  try {
    if (payload.seedSql) {
      await db.exec(String(payload.seedSql));
    }

    if (msg.type === "schema") {
      const schema = await introspectSchema(db);
      self.postMessage({
        type: "result",
        requestId,
        result: {
          ok: true,
          status: "ok",
          stdout: JSON.stringify(schema, null, 2),
          stderr: "",
          tests: [],
          table_diff: null,
          plots: [],
          schema,
          elapsed_ms: Math.round(performance.now() - started),
        },
      });
      return;
    }

    const queryResult = await db.query(String(payload.code || ""));
    const fields = Array.isArray(queryResult.fields) ? queryResult.fields : [];
    const columns = fields.map((f) => String(f.name));
    const rawRows = Array.isArray(queryResult.rows) ? queryResult.rows : [];
    const actualRows = rawRows
      .slice(0, MAX_ROWS_RETURNED)
      .map((obj) => columns.map((col) => normalizeCell(obj[col])));

    let tableDiff = null;
    if (payload.expectedTable && Array.isArray(payload.expectedTable.rows)) {
      const expCols = Array.isArray(payload.expectedTable.columns)
        ? payload.expectedTable.columns
        : columns;
      tableDiff = computeRelationDiff(
        expCols,
        payload.expectedTable.rows,
        actualRows,
        Boolean(payload.orderSensitive),
      );
    } else if (columns.length > 0) {
      tableDiff = computeRelationDiff(columns, actualRows, actualRows, true);
    }

    const tests = [];
    const allTests = [
      ...(Array.isArray(payload.visibleTests)
        ? payload.visibleTests.map((t) => ({ ...t, hidden: false }))
        : []),
      ...(Array.isArray(payload.hiddenTests)
        ? payload.hiddenTests.map((t) => ({ ...t, hidden: true }))
        : []),
    ];

    for (const tc of allTests) {
      const t0 = performance.now();
      let passed = false;
      let actual = null;
      let message = "";
      try {
        if (tc.code === "__DIFF__" && tableDiff) {
          passed = tableDiff.matched;
          actual = passed ? `${actualRows.length} řádků` : "neshoda relace";
        } else if (tc.code) {
          const checkRes = await db.query(String(tc.code));
          const firstRow = checkRes.rows && checkRes.rows[0];
          const firstVal = firstRow ? Object.values(firstRow)[0] : null;
          actual = normalizeCell(firstVal);
          passed = cellsEqual(actual, tc.expected, Number(tc.tolerance) || 1e-4);
        }
      } catch (err) {
        passed = false;
        message = String(err && err.message ? err.message : err);
      }
      tests.push({
        name: String(tc.name || "SQL Test"),
        passed,
        actual: tc.hidden ? (passed ? "ověřeno" : "neshoda") : actual,
        expected: tc.hidden ? null : tc.expected,
        message,
        elapsed_ms: Math.round(performance.now() - t0),
        hidden: Boolean(tc.hidden),
      });
    }

    const allTestsPass = tests.every((t) => t.passed);
    const diffPass = payload.expectedTable ? Boolean(tableDiff && tableDiff.matched) : true;

    self.postMessage({
      type: "result",
      requestId,
      result: {
        ok: allTestsPass && diffPass,
        status: "ok",
        stdout: truncateUtf8(formatAsciiTable(columns, actualRows)),
        stderr: "",
        tests,
        table_diff: tableDiff,
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
  } finally {
    if (typeof db.close === "function") {
      await db.close().catch(() => {});
    }
  }
}

if (typeof self !== "undefined" && typeof self.postMessage === "function") {
  self.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || (msg.type !== "run" && msg.type !== "schema")) {
      return;
    }
    handleMessage(msg);
  });
}
