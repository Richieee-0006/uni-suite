/**
 * Tier 1 WebR WASM Worker for R (KI/NUM, KI/PAS).
 * - Initializes WebR lazily on first execution.
 * - Applies theme-matched R par(bg, fg, col.axis, col.lab, col.main) for dark/light plots.
 * - Captures ONLY raster PNG buffers via R png() / OffscreenCanvas (NEVER raw SVG!).
 * - Runs visible_tests and hidden_tests with numeric tolerance.
 * - Extracts data.frame outputs for Expected vs. Actual Result Diff.
 * - Hard timeout is enforced on the main thread via worker.terminate().
 */

const DEFAULT_WEBR_MODULE_URL = "/static/vendor/webr/webr.mjs";
const MAX_STDOUT_BYTES = 32 * 1024;
const MAX_PLOTS = 8;
const MAX_PLOT_BYTES = 512 * 1024;
const PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

let webrInstance = null;
let webrInitPromise = null;

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

function isValidPng(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 24 || bytes.byteLength > MAX_PLOT_BYTES) {
    return false;
  }
  for (let i = 0; i < PNG_MAGIC.length; i += 1) {
    if (bytes[i] !== PNG_MAGIC[i]) {
      return false;
    }
  }
  return (
    bytes[12] === 0x49 &&
    bytes[13] === 0x48 &&
    bytes[14] === 0x44 &&
    bytes[15] === 0x52
  );
}

function rQuote(str) {
  return (
    '"' +
    String(str || "")
      .replace(/\\/g, "\\\\")
      .replace(/"/g, '\\"')
      .replace(/\r/g, "\\r")
      .replace(/\n/g, "\\n")
      .replace(/\t/g, "\\t") +
    '"'
  );
}

function rScalar(val) {
  if (val === null || val === undefined) {
    return "NULL";
  }
  if (typeof val === "boolean") {
    return val ? "TRUE" : "FALSE";
  }
  if (typeof val === "number" && Number.isFinite(val)) {
    return String(val);
  }
  return rQuote(String(val));
}

function cellsEqual(a, b, tol = 1e-4) {
  if (a === null && b === null) {
    return true;
  }
  if (a === undefined && b === undefined) {
    return true;
  }
  if (typeof a === "number" && typeof b === "number") {
    if (Number.isNaN(a) && Number.isNaN(b)) {
      return true;
    }
    return Math.abs(a - b) <= tol;
  }
  return String(a) === String(b);
}

function buildTableDiff(expectedTable, actualTable, tolerance = 1e-4) {
  if (!actualTable || !Array.isArray(actualTable.columns)) {
    return null;
  }
  const columns =
    expectedTable && Array.isArray(expectedTable.columns) && expectedTable.columns.length
      ? expectedTable.columns.map(String)
      : actualTable.columns.map(String);
  const actualRows = Array.isArray(actualTable.rows) ? actualTable.rows : [];
  const expectedRows =
    expectedTable && Array.isArray(expectedTable.rows)
      ? expectedTable.rows
      : actualRows;

  const maxLen = Math.max(expectedRows.length, actualRows.length);
  const rowMatches = [];
  for (let i = 0; i < maxLen; i += 1) {
    if (i >= expectedRows.length || i >= actualRows.length) {
      rowMatches.push(false);
      continue;
    }
    const expRow = expectedRows[i];
    const actRow = actualRows[i];
    if (!Array.isArray(expRow) || !Array.isArray(actRow) || expRow.length !== actRow.length) {
      rowMatches.push(false);
      continue;
    }
    rowMatches.push(
      actRow.every((cell, idx) => cellsEqual(cell, expRow[idx], tolerance)),
    );
  }

  return {
    columns,
    expected_rows: expectedRows,
    actual_rows: actualRows,
    row_matches: rowMatches,
    matched: expectedRows.length === actualRows.length && rowMatches.every(Boolean),
  };
}

async function ensureWebR(config, requestId) {
  if (webrInstance) {
    return webrInstance;
  }
  if (!webrInitPromise) {
    webrInitPromise = (async () => {
      const moduleUrl =
        (config && typeof config.moduleUrl === "string" && config.moduleUrl) ||
        DEFAULT_WEBR_MODULE_URL;
      self.postMessage({
        type: "status",
        requestId,
        message: "Inicializuji lokální R (WebR WASM)…",
      });
      const mod = await import(moduleUrl);
      const WebR = mod.WebR || (mod.default && mod.default.WebR);
      if (typeof WebR !== "function") {
        throw new Error("WebR modul neexportuje konstruktor WebR.");
      }
      const options = {};
      if (config && typeof config.baseUrl === "string" && config.baseUrl) {
        options.baseUrl = config.baseUrl;
      }
      const instance = new WebR(options);
      await instance.init();
      webrInstance = instance;
      return instance;
    })().catch((err) => {
      webrInitPromise = null;
      throw err;
    });
  }
  return webrInitPromise;
}

function buildRRunnerScript(payload) {
  const isLight = payload.theme === "light";
  const bg = isLight ? "#ffffff" : "#121315";
  const fg = isLight ? "#13161c" : "#e6e8ec";
  const axis = isLight ? "#4b5563" : "#9aa1ad";

  const combinedTests = [
    ...(Array.isArray(payload.visibleTests)
      ? payload.visibleTests.map((t) => ({ ...t, hidden: false }))
      : []),
    ...(Array.isArray(payload.hiddenTests)
      ? payload.hiddenTests.map((t) => ({ ...t, hidden: true }))
      : []),
  ];

  const testsR = combinedTests
    .map(
      (tc) =>
        `list(name=${rQuote(tc.name || "Test")}, code=${rQuote(tc.code || "")}, ` +
        `expected=${rScalar(tc.expected)}, tol=${Number.isFinite(Number(tc.tolerance)) ? Number(tc.tolerance) : 1e-4}, ` +
        `hidden=${tc.hidden ? "TRUE" : "FALSE"})`,
    )
    .join(", ");

  return `
local({
  plot_dir <- "/tmp/uni_webr_plots"
  if (dir.exists(plot_dir)) unlink(plot_dir, recursive = TRUE, force = TRUE)
  dir.create(plot_dir, showWarnings = FALSE, recursive = TRUE)

  json_esc <- function(s) {
    s <- gsub("\\\\", "\\\\\\\\", s, fixed = TRUE)
    s <- gsub("\\"", "\\\\\\"", s, fixed = TRUE)
    s <- gsub("\\n", "\\\\n", s, fixed = TRUE)
    s <- gsub("\\r", "\\\\r", s, fixed = TRUE)
    s <- gsub("\\t", "\\\\t", s, fixed = TRUE)
    paste0("\\"", s, "\\"")
  }
  to_json <- function(x) {
    if (is.null(x)) return("null")
    if (is.logical(x) && length(x) == 1L) return(if (is.na(x)) "null" else if (x) "true" else "false")
    if (is.numeric(x) && length(x) == 1L) return(if (!is.finite(x)) "null" else format(x, digits = 12, trim = TRUE, scientific = FALSE))
    if (is.character(x) && length(x) == 1L) return(if (is.na(x)) "null" else json_esc(x))
    if (is.list(x)) {
      nms <- names(x)
      if (!is.null(nms) && all(nzchar(nms))) {
        parts <- vapply(seq_along(x), function(i) paste0(json_esc(nms[[i]]), ":", to_json(x[[i]])), character(1))
        return(paste0("{", paste(parts, collapse = ","), "}"))
      }
      parts <- vapply(x, to_json, character(1))
      return(paste0("[", paste(parts, collapse = ","), "]"))
    }
    if (length(x) > 1L) {
      parts <- vapply(as.list(x), to_json, character(1))
      return(paste0("[", paste(parts, collapse = ","), "]"))
    }
    json_esc(as.character(x))
  }

  png_ok <- FALSE
  tryCatch({
    grDevices::png(
      filename = file.path(plot_dir, "plot_%02d.png"),
      width = 960,
      height = 600,
      res = 144,
      bg = ${rQuote(bg)}
    )
    graphics::par(
      bg = ${rQuote(bg)},
      fg = ${rQuote(fg)},
      col = ${rQuote(fg)},
      col.axis = ${rQuote(axis)},
      col.lab = ${rQuote(fg)},
      col.main = ${rQuote(fg)},
      col.sub = ${rQuote(axis)}
    )
    png_ok <- TRUE
  }, error = function(e) {
    png_ok <<- FALSE
  })

  user_env <- new.env(parent = globalenv())
  run_err <- ""
  last_val <- NULL
  out_lines <- capture.output({
    tryCatch({
      exprs <- parse(text = ${rQuote(payload.code)})
      for (ex in exprs) {
        last_val <- eval(ex, envir = user_env)
      }
    }, error = function(e) {
      run_err <<- conditionMessage(e)
    })
  })

  if (png_ok && grDevices::dev.cur() > 1L) {
    try(grDevices::dev.off(), silent = TRUE)
  }

  tests_spec <- list(${testsR})
  test_results <- list()
  if (!nzchar(run_err) && length(tests_spec) > 0L) {
    for (tc in tests_spec) {
      t0 <- proc.time()[["elapsed"]]
      item <- list(
        name = tc$name,
        passed = FALSE,
        actual = NULL,
        expected = if (isTRUE(tc$hidden)) NULL else tc$expected,
        message = "",
        elapsed_ms = 0L,
        hidden = isTRUE(tc$hidden)
      )
      tryCatch({
        val <- eval(parse(text = tc$code), envir = user_env)
        if (is.numeric(val) && length(val) == 1L && is.numeric(tc$expected)) {
          ok_num <- is.finite(val) && abs(as.numeric(val) - as.numeric(tc$expected)) <= as.numeric(tc$tol)
          item$passed <- ok_num
          item$actual <- if (isTRUE(tc$hidden)) (if (ok_num) "ověřeno" else "neshoda") else as.numeric(val)
        } else if (is.logical(val) && length(val) == 1L && is.null(tc$expected)) {
          item$passed <- isTRUE(val)
          item$actual <- if (isTRUE(tc$hidden)) (if (isTRUE(val)) "ověřeno" else "neshoda") else isTRUE(val)
        } else {
          ok_eq <- identical(as.character(val), as.character(tc$expected))
          item$passed <- ok_eq
          item$actual <- if (isTRUE(tc$hidden)) (if (ok_eq) "ověřeno" else "neshoda") else paste(as.character(val), collapse = ", ")
        }
      }, error = function(e) {
        item$passed <<- FALSE
        item$message <<- conditionMessage(e)
      })
      item$elapsed_ms <- as.integer(round((proc.time()[["elapsed"]] - t0) * 1000))
      test_results[[length(test_results) + 1L]] <- item
    }
  }

  table_out <- NULL
  df_obj <- NULL
  if (is.data.frame(last_val)) {
    df_obj <- last_val
  } else if (exists("result_df", envir = user_env, inherits = FALSE)) {
    cand <- get("result_df", envir = user_env, inherits = FALSE)
    if (is.data.frame(cand)) df_obj <- cand
  }
  if (!is.null(df_obj)) {
    nr <- min(nrow(df_obj), 100L)
    rows <- vector("list", nr)
    for (i in seq_len(nr)) {
      rows[[i]] <- lapply(seq_len(ncol(df_obj)), function(j) {
        v <- df_obj[i, j]
        if (is.factor(v)) as.character(v) else v
      })
    }
    table_out <- list(columns = colnames(df_obj), rows = rows)
  }

  plot_files <- sort(list.files(plot_dir, pattern = "\\\\.png$", full.names = TRUE))
  if (length(plot_files) > 8L) plot_files <- plot_files[seq_len(8L)]

  to_json(list(
    ok = !nzchar(run_err),
    stdout = paste(out_lines, collapse = "\\n"),
    stderr = run_err,
    tests = test_results,
    table = table_out,
    plot_files = as.list(plot_files)
  ))
})
`;
}

async function handleRun(requestId, config, payload) {
  const started = performance.now();
  let webR;
  try {
    webR = await ensureWebR(config, requestId);
  } catch (err) {
    self.postMessage({
      type: "result",
      requestId,
      result: {
        ok: false,
        status: "unavailable",
        stdout: "",
        stderr:
          "WebR WASM runtime není v tomto prohlížeči nainstalován nebo dostupný: " +
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

  const shelter = await new webR.Shelter();
  try {
    const rScript = buildRRunnerScript(payload);
    const jsonResult = await shelter.evalRString(rScript);
    const parsed = JSON.parse(jsonResult);

    const plots = [];
    const transferables = [];
    const plotFiles = Array.isArray(parsed.plot_files) ? parsed.plot_files : [];
    for (let i = 0; i < plotFiles.length && plots.length < MAX_PLOTS; i += 1) {
      const fpath = String(plotFiles[i] || "");
      if (!fpath.startsWith("/tmp/uni_webr_plots/")) {
        continue;
      }
      try {
        const rawBytes = await webR.FS.readFile(fpath);
        const u8 = rawBytes instanceof Uint8Array ? rawBytes : new Uint8Array(rawBytes);
        if (isValidPng(u8)) {
          const copy = new Uint8Array(u8.byteLength);
          copy.set(u8);
          plots.push({
            bytes: copy.buffer,
            caption: `Graf ${plots.length + 1}`,
          });
          transferables.push(copy.buffer);
        }
      } catch {
        // Skip unreadable temporary file.
      }
    }

    const tests = Array.isArray(parsed.tests) ? parsed.tests : [];
    const allTestsPass = tests.length === 0 || tests.every((t) => Boolean(t.passed));
    const tableDiff = parsed.table
      ? buildTableDiff(payload.expectedTable, parsed.table)
      : null;

    self.postMessage(
      {
        type: "result",
        requestId,
        result: {
          ok: Boolean(parsed.ok) && allTestsPass,
          status: parsed.ok ? "ok" : "error",
          stdout: truncateUtf8(parsed.stdout || ""),
          stderr: truncateUtf8(parsed.stderr || "", 16 * 1024),
          tests,
          table_diff: tableDiff,
          plots,
          elapsed_ms: Math.round(performance.now() - started),
        },
      },
      transferables,
    );
  } catch (err) {
    self.postMessage({
      type: "result",
      requestId,
      result: {
        ok: false,
        status: "error",
        stdout: "",
        stderr: truncateUtf8(
          `Chyba při provádění R kódu: ${String(err && err.message ? err.message : err)}`,
          16 * 1024,
        ),
        tests: [],
        table_diff: null,
        plots: [],
        elapsed_ms: Math.round(performance.now() - started),
      },
    });
  } finally {
    await shelter.purge();
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
