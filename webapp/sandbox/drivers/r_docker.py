"""Ephemeral Docker driver for isolated server-side R execution & PNG plots."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import time
from typing import Any

from ..base import (
    MAX_PLOT_BYTES,
    MAX_PLOTS,
    MAX_STDOUT_BYTES,
    ExecutionRequest,
    ExecutionResult,
    LanguageDriver,
    PlotArtifact,
    TestResult,
    ValidationError,
    compute_table_diff,
)

DEFAULT_R_IMAGE = "r-base:4.4.1"
IMAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
RESULT_SENTINEL = "__UNI_SANDBOX_JSON_START__"

# Pure base-R harness: zero third-party R packages required.
# Reads JSON payload from stdin, runs student code in a clean child env,
# captures PNG plots inside container tmpfs /tmp, encodes them in base64,
# runs visible/hidden tests, and emits a delimited JSON footer.
_R_HARNESS_TEMPLATE = r"""
local({
  raw_in <- paste(readLines(file("stdin"), warn = FALSE), collapse = "\n")
  if (!nzchar(raw_in)) stop("Empty sandbox payload")

  b64_encode <- function(raw_vec) {
    n <- length(raw_vec)
    if (n == 0L) return("")
    alphabet <- strsplit(
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
      ""
    )[[1]]
    vals <- as.integer(raw_vec)
    pad <- (3L - (n %% 3L)) %% 3L
    if (pad > 0L) vals <- c(vals, integer(pad))
    m <- matrix(vals, nrow = 3L)
    b1 <- bitwShiftR(m[1L, ], 2L)
    b2 <- bitwOr(bitwShiftL(bitwAnd(m[1L, ], 3L), 4L), bitwShiftR(m[2L, ], 4L))
    b3 <- bitwOr(bitwShiftL(bitwAnd(m[2L, ], 15L), 2L), bitwShiftR(m[3L, ], 6L))
    b4 <- bitwAnd(m[3L, ], 63L)
    idx <- as.vector(rbind(b1, b2, b3, b4)) + 1L
    chars <- alphabet[idx]
    if (pad >= 1L) chars[length(chars)] <- "="
    if (pad == 2L) chars[length(chars) - 1L] <- "="
    paste(chars, collapse = "")
  }

  json_escape <- function(s) {
    s <- gsub("\\", "\\\\", s, fixed = TRUE)
    s <- gsub("\"", "\\\"", s, fixed = TRUE)
    s <- gsub("\n", "\\n", s, fixed = TRUE)
    s <- gsub("\r", "\\r", s, fixed = TRUE)
    s <- gsub("\t", "\\t", s, fixed = TRUE)
    paste0("\"", s, "\"")
  }

  to_json <- function(x) {
    if (is.null(x)) return("null")
    if (is.logical(x) && length(x) == 1L) {
      if (is.na(x)) return("null")
      return(if (x) "true" else "false")
    }
    if (is.numeric(x) && length(x) == 1L) {
      if (!is.finite(x)) return("null")
      return(format(x, digits = 12, trim = TRUE, scientific = FALSE))
    }
    if (is.character(x) && length(x) == 1L) {
      if (is.na(x)) return("null")
      return(json_escape(x))
    }
    if (is.list(x)) {
      nms <- names(x)
      if (!is.null(nms) && all(nzchar(nms))) {
        parts <- vapply(
          seq_along(x),
          function(i) paste0(json_escape(nms[[i]]), ":", to_json(x[[i]])),
          character(1)
        )
        return(paste0("{", paste(parts, collapse = ","), "}"))
      }
      parts <- vapply(x, to_json, character(1))
      return(paste0("[", paste(parts, collapse = ","), "]"))
    }
    if (length(x) > 1L) {
      parts <- vapply(as.list(x), to_json, character(1))
      return(paste0("[", paste(parts, collapse = ","), "]"))
    }
    json_escape(as.character(x))
  }

  payload_env <- new.env(parent = baseenv())
  eval(parse(text = raw_in), envir = payload_env)
  code_str <- payload_env$CODE
  theme_str <- payload_env$THEME
  tests_list <- payload_env$TESTS

  plot_dir <- "/tmp/uni_plots"
  dir.create(plot_dir, showWarnings = FALSE, recursive = TRUE)
  plot_pattern <- file.path(plot_dir, "plot_%02d.png")

  is_light <- identical(theme_str, "light")
  bg_col <- if (is_light) "#ffffff" else "#121315"
  fg_col <- if (is_light) "#13161c" else "#e6e8ec"
  axis_col <- if (is_light) "#4b5563" else "#9aa1ad"

  png_ok <- FALSE
  tryCatch({
    grDevices::png(
      filename = plot_pattern,
      width = 800,
      height = 500,
      res = 120,
      bg = bg_col
    )
    graphics::par(
      bg = bg_col,
      fg = fg_col,
      col = fg_col,
      col.axis = axis_col,
      col.lab = fg_col,
      col.main = fg_col,
      col.sub = axis_col
    )
    png_ok <- TRUE
  }, error = function(e) {
    png_ok <<- FALSE
  })

  user_env <- new.env(parent = globalenv())
  run_error <- ""
  last_val <- NULL
  stdout_lines <- capture.output({
    tryCatch({
      exprs <- parse(text = code_str)
      for (ex in exprs) {
        last_val <- eval(ex, envir = user_env)
      }
    }, error = function(e) {
      run_error <<- conditionMessage(e)
    })
  })

  if (png_ok && grDevices::dev.cur() > 1L) {
    try(grDevices::dev.off(), silent = TRUE)
  }

  test_results <- list()
  if (!nzchar(run_error) && length(tests_list) > 0L) {
    for (tc in tests_list) {
      t0 <- proc.time()[["elapsed"]]
      res <- list(
        name = tc$name,
        passed = FALSE,
        actual = NULL,
        expected = tc$expected,
        message = "",
        elapsed_ms = 0L,
        hidden = isTRUE(tc$hidden)
      )
      tryCatch({
        val <- eval(parse(text = tc$code), envir = user_env)
        if (is.numeric(val) && length(val) == 1L && is.numeric(tc$expected)) {
          res$actual <- as.numeric(val)
          res$passed <- is.finite(val) &&
            abs(as.numeric(val) - as.numeric(tc$expected)) <= as.numeric(tc$tol)
        } else if (is.logical(val) && length(val) == 1L && is.null(tc$expected)) {
          res$actual <- isTRUE(val)
          res$passed <- isTRUE(val)
        } else {
          res$actual <- paste(as.character(val), collapse = ", ")
          res$passed <- identical(
            as.character(val),
            as.character(tc$expected)
          )
        }
      }, error = function(e) {
        res$passed <<- FALSE
        res$message <<- conditionMessage(e)
      })
      res$elapsed_ms <- as.integer(round((proc.time()[["elapsed"]] - t0) * 1000))
      test_results[[length(test_results) + 1L]] <- res
    }
  }

  table_out <- NULL
  df_candidate <- NULL
  if (is.data.frame(last_val)) {
    df_candidate <- last_val
  } else if (exists("result_df", envir = user_env, inherits = FALSE)) {
    cand <- get("result_df", envir = user_env, inherits = FALSE)
    if (is.data.frame(cand)) df_candidate <- cand
  }
  if (!is.null(df_candidate)) {
    nr <- min(nrow(df_candidate), 100L)
    rows_out <- vector("list", nr)
    for (i in seq_len(nr)) {
      rows_out[[i]] <- lapply(seq_len(ncol(df_candidate)), function(j) {
        v <- df_candidate[i, j]
        if (is.factor(v)) as.character(v) else v
      })
    }
    table_out <- list(columns = colnames(df_candidate), rows = rows_out)
  }

  plots_b64 <- list()
  plot_files <- sort(list.files(plot_dir, pattern = "\\.png$", full.names = TRUE))
  if (length(plot_files) > 8L) plot_files <- plot_files[seq_len(8L)]
  for (pf in plot_files) {
    sz <- file.info(pf)$size
    if (!is.na(sz) && sz >= 24L && sz <= 524288L) {
      raw_bytes <- readBin(pf, what = "raw", n = sz)
      plots_b64[[length(plots_b64) + 1L]] <- b64_encode(raw_bytes)
    }
  }

  out_obj <- list(
    ok = !nzchar(run_error),
    stdout = paste(stdout_lines, collapse = "\n"),
    stderr = run_error,
    tests = test_results,
    table = table_out,
    plots_b64 = plots_b64
  )
  cat("\n__UNI_SANDBOX_JSON_START__\n")
  cat(to_json(out_obj))
  cat("\n")
})
"""


def _r_str_literal(value: str) -> str:
    """Safely serialize a Python string as an R double-quoted string literal."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _r_scalar_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(float(value))
    return _r_str_literal(str(value))


def _build_r_stdin_payload(request: ExecutionRequest) -> bytes:
    """Build pure R assignment payload fed over stdin (never shell-interpolated)."""
    lines = [
        f"CODE <- {_r_str_literal(request.code)}",
        f"THEME <- {_r_str_literal(request.theme)}",
    ]
    test_exprs: list[str] = []
    for tc in request.tests:
        test_exprs.append(
            "list("
            f"name={_r_str_literal(tc.name)}, "
            f"code={_r_str_literal(tc.code)}, "
            f"expected={_r_scalar_literal(tc.expected)}, "
            f"tol={tc.tolerance!r}, "
            f"hidden={'TRUE' if tc.hidden else 'FALSE'}"
            ")"
        )
    lines.append(f"TESTS <- list({', '.join(test_exprs)})")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _stripped_host_env() -> dict[str, str]:
    """Strip application secrets before invoking docker CLI."""
    env: dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    docker_host = os.environ.get("DOCKER_HOST")
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env


class RDockerDriver(LanguageDriver):
    """Tier 2 ephemeral Docker driver for R (`r-base`) with PNG plot capture."""

    driver_id = "r_docker"
    language = "r"
    display_name = "R (Docker r-base)"
    tier = "tier2_docker"
    max_timeout_sec = 10.0

    def __init__(self, image: str | None = None) -> None:
        candidate = (
            image or os.getenv("SANDBOX_R_IMAGE", DEFAULT_R_IMAGE)
        ).strip()
        if not IMAGE_NAME_RE.match(candidate):
            raise ValidationError("Invalid SANDBOX_R_IMAGE identifier.")
        self.image = candidate

    def _docker_binary(self) -> str | None:
        return shutil.which("docker")

    def is_available(self) -> tuple[bool, str]:
        if os.getenv("SANDBOX_DOCKER_ENABLED", "0").strip() != "1":
            return (
                False,
                "Tier 2 Docker sandbox je vypnutý (SANDBOX_DOCKER_ENABLED != 1).",
            )
        docker_bin = self._docker_binary()
        if not docker_bin:
            return False, "Docker CLI nebylo na serveru nalezeno."
        return True, ""

    def build_docker_command(
        self,
        container_name: str,
        docker_bin: str = "docker",
    ) -> list[str]:
        """Construct the strictly hardened ``docker run`` argument vector."""
        tmpfs_spec = "/tmp:rw,noexec,nosuid,size=32m"  # noqa: S108
        return [
            docker_bin,
            "run",
            "--rm",
            "--init",
            "--interactive",
            "--name",
            container_name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--memory=256m",
            "--memory-swap=256m",
            "--cpus=0.5",
            "--pids-limit=64",
            "--tmpfs",
            tmpfs_spec,
            "--env",
            "HOME=/tmp",  # noqa: S108
            "--env",
            "TMPDIR=/tmp",  # noqa: S108
            "--env",
            "R_USER=/tmp",  # noqa: S108
            self.image,
            "Rscript",
            "--vanilla",
            "--no-save",
            "--no-restore",
            "-e",
            _R_HARNESS_TEMPLATE,
        ]

    async def _force_remove_container(
        self,
        docker_bin: str,
        container_name: str,
    ) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                docker_bin,
                "rm",
                "-f",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=_stripped_host_env(),
            )
            await asyncio.wait_for(proc.communicate(), timeout=3.0)
        except (OSError, TimeoutError):
            pass

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        docker_bin = self._docker_binary()
        if not docker_bin:
            return ExecutionResult(
                ok=False,
                status="unavailable",
                stderr="Docker CLI není dostupné.",
                driver=self.driver_id,
            )

        container_name = f"uni-sbx-r-{secrets.token_hex(8)}"
        cmd = self.build_docker_command(container_name, docker_bin=docker_bin)
        stdin_payload = _build_r_stdin_payload(request)
        started = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_stripped_host_env(),
            )
        except OSError as exc:
            return ExecutionResult(
                ok=False,
                status="unavailable",
                stderr=f"Nepodařilo se spustit Docker daemon: {exc}",
                driver=self.driver_id,
            )

        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(input=stdin_payload),
                timeout=request.timeout_sec,
            )
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await self._force_remove_container(docker_bin, container_name)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return ExecutionResult(
                ok=False,
                status="timeout",
                stderr=(
                    f"Překročen časový limit běhu ({request.timeout_sec:g} s)."
                ),
                elapsed_ms=elapsed_ms,
                driver=self.driver_id,
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout_text = raw_out.decode("utf-8", errors="replace")
        stderr_text = raw_err.decode("utf-8", errors="replace")

        if proc.returncode != 0 and RESULT_SENTINEL not in stdout_text:
            low_err = stderr_text.lower()
            if "cannot connect to the docker daemon" in low_err or (
                "unable to find image" in low_err
            ):
                return ExecutionResult(
                    ok=False,
                    status="unavailable",
                    stderr="Docker daemon nebo obraz r-base není připraven.",
                    elapsed_ms=elapsed_ms,
                    driver=self.driver_id,
                )
            return ExecutionResult(
                ok=False,
                status="error",
                stdout=stdout_text[:MAX_STDOUT_BYTES],
                stderr=stderr_text or f"R skončil s kódem {proc.returncode}.",
                elapsed_ms=elapsed_ms,
                driver=self.driver_id,
            )

        return self._parse_harness_output(
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            request=request,
            elapsed_ms=elapsed_ms,
        )

    def _parse_harness_output(
        self,
        stdout_text: str,
        stderr_text: str,
        request: ExecutionRequest,
        elapsed_ms: int,
    ) -> ExecutionResult:
        if RESULT_SENTINEL not in stdout_text:
            return ExecutionResult(
                ok=False,
                status="error",
                stdout=stdout_text,
                stderr=stderr_text or "Chybí strukturovaný výstup R harnessu.",
                elapsed_ms=elapsed_ms,
                driver=self.driver_id,
            )

        _, json_tail = stdout_text.rsplit(RESULT_SENTINEL, 1)
        try:
            parsed = json.loads(json_tail.strip())
        except json.JSONDecodeError:
            return ExecutionResult(
                ok=False,
                status="error",
                stderr="Nepodařilo se zpracovat JSON výstup R sandboxu.",
                elapsed_ms=elapsed_ms,
                driver=self.driver_id,
            )

        plots: list[PlotArtifact] = []
        raw_plots = parsed.get("plots_b64") or []
        if isinstance(raw_plots, list):
            for idx, b64_item in enumerate(raw_plots[:MAX_PLOTS], start=1):
                if not isinstance(b64_item, str):
                    continue
                if len(b64_item) > (MAX_PLOT_BYTES * 2):
                    continue
                try:
                    raw_png = base64.b64decode(b64_item, validate=True)
                    plots.append(
                        PlotArtifact(data=raw_png, caption=f"Graf {idx}")
                    )
                except (ValueError, ValidationError):
                    # Drop any invalid or forged non-PNG artifact silently.
                    continue

        tests_out: list[TestResult] = []
        for item in parsed.get("tests") or []:
            if not isinstance(item, dict):
                continue
            tests_out.append(
                TestResult(
                    name=str(item.get("name", "Test")),
                    passed=bool(item.get("passed", False)),
                    actual=item.get("actual"),
                    expected=item.get("expected"),
                    message=str(item.get("message") or ""),
                    elapsed_ms=int(item.get("elapsed_ms") or 0),
                    hidden=bool(item.get("hidden", False)),
                )
            )

        table_diff = None
        table_data = parsed.get("table")
        if (
            isinstance(table_data, dict)
            and isinstance(request.expected_table, dict)
        ):
            cols = table_data.get("columns") or request.expected_table.get(
                "columns", []
            )
            act_rows = table_data.get("rows") or []
            exp_rows = request.expected_table.get("rows") or []
            table_diff = compute_table_diff(cols, exp_rows, act_rows)

        run_ok = bool(parsed.get("ok", False))
        all_tests_ok = all(t.passed for t in tests_out) if tests_out else True
        err_msg = str(parsed.get("stderr") or "").strip()
        if not err_msg and stderr_text.strip() and not run_ok:
            err_msg = stderr_text.strip()

        return ExecutionResult(
            ok=run_ok and all_tests_ok,
            status="ok" if run_ok else "error",
            stdout=str(parsed.get("stdout") or ""),
            stderr=err_msg,
            tests=tests_out,
            table_diff=table_diff,
            plots=plots,
            elapsed_ms=elapsed_ms,
            driver=self.driver_id,
        )
