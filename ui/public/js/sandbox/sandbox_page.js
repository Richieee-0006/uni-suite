/**
 * ES Module bridge connecting polyglot sandbox workers + UI components
 * to window.UniSandbox and hydrating /predmet/{slug}/code.
 * Strict CSP / Trusted Types 'none' safe: pure DOM node construction only.
 */

import { defaultSandboxRegistry } from "./driver_registry.js";
import { SandboxCodeEditor } from "./code_editor.js";
import { SandboxPlotViewer } from "./plot_viewer.js";
import { SandboxResultDiff } from "./result_diff.js";

window.UniSandbox = defaultSandboxRegistry;
window.UniSuite = window.UniSuite || {};
window.UniSuite.sandbox = defaultSandboxRegistry;

function readAppState() {
  const node = document.getElementById("uni-state");
  if (!node) return {};
  try {
    return JSON.parse(node.textContent || "{}");
  } catch {
    return {};
  }
}

function clearEl(node) {
  while (node && node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function makeEl(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = String(text);
  return el;
}

function activePlotTheme() {
  const theme = document.documentElement.dataset.theme || "graphite";
  return theme === "light" ? "light" : "dark";
}

async function initStandaloneSandboxPage() {
  const state = readAppState();
  if (!state.is_code_route || !state.subject || !state.subject.slug) {
    return;
  }

  const editorHost = document.getElementById("code-editor-host");
  const testsOut = document.getElementById("sandbox-out-tests");
  const consoleOut = document.getElementById("sandbox-out-console");
  const diffOut = document.getElementById("sandbox-out-diff");
  const plotsOut = document.getElementById("sandbox-out-plots");
  if (!editorHost || !testsOut || !consoleOut || !diffOut || !plotsOut) {
    return;
  }

  const taskPicker = document.getElementById("sandbox-task-picker");
  const langPicker = document.getElementById("sandbox-lang-picker");
  const runtimeLabel = document.getElementById("sandbox-runtime-label");
  const metaLine = document.getElementById("sandbox-meta-line");
  const filenameEl = document.getElementById("sandbox-filename");
  const taskTitle = document.getElementById("sandbox-task-title");
  const taskDesc = document.getElementById("sandbox-task-desc");
  const taskFormula = document.getElementById("sandbox-task-formula");
  const taskHints = document.getElementById("sandbox-task-hints");
  const resetBtn = document.getElementById("sandbox-reset-btn");
  const runBtn = document.getElementById("sandbox-run-btn");
  const testBtn = document.getElementById("sandbox-test-btn");
  const mobileRunBtn = document.querySelector("[data-sandbox-run-mobile]");

  const LANG_META = {
    r: {
      label: "R (WebR)",
      runtime: "WebR WASM · izolováno",
      filename: "solution.R",
    },
    python: {
      label: "Python (Pyodide)",
      runtime: "Pyodide WASM · izolováno",
      filename: "solution.py",
    },
  };

  const subjectLangs =
    state.subject &&
    state.subject.sandbox &&
    Array.isArray(state.subject.sandbox.languages) &&
    state.subject.sandbox.languages.length > 0
      ? state.subject.sandbox.languages.map((l) => String(l).toLowerCase())
      : ["r"];
  let activeLang = subjectLangs[0] || "r";

  const defaultStarter =
    "# Složene lichobeznikove pravidlo v R\n" +
    "trapezoid <- function(f, a, b, n) {\n" +
    "  h <- (b - a) / n\n" +
    "  x <- seq(a, b, length.out = n + 1)\n" +
    "  y <- f(x)\n" +
    "  (h / 2) * (y[1] + 2 * sum(y[2:n]) + y[n + 1])\n" +
    "}\n";

  const editor = new SandboxCodeEditor(editorHost, {
    language: activeLang,
    starterCode: defaultStarter,
    onRun: () => runSandbox(true),
  });
  const plotViewer = new SandboxPlotViewer(plotsOut, {
    theme: activePlotTheme(),
    onThemeChange: () => {
      if (lastRunWithTests !== null) {
        runSandbox(lastRunWithTests);
      }
    },
  });
  const resultDiff = new SandboxResultDiff(diffOut);

  let tasks = [];
  let currentTask = null;
  let running = false;
  let lastRunWithTests = null;

  function resolveTaskVariant(task, lang) {
    if (!task) {
      return { language: lang, starter_code: defaultStarter, visible_tests: [] };
    }
    const variants = task.languages && typeof task.languages === "object" ? task.languages : null;
    if (variants && variants[lang] && typeof variants[lang] === "object") {
      const v = variants[lang];
      return {
        language: lang,
        starter_code: v.starter_code || task.starter_code || defaultStarter,
        visible_tests: Array.isArray(v.visible_tests) ? v.visible_tests : task.visible_tests || [],
      };
    }
    const fallbackLang = (task.language || lang || "r").toLowerCase();
    return {
      language: fallbackLang,
      starter_code: task.starter_code || defaultStarter,
      visible_tests: Array.isArray(task.visible_tests) ? task.visible_tests : [],
    };
  }

  function updateLanguageChrome(lang) {
    const info = LANG_META[lang] || {
      label: lang.toUpperCase(),
      runtime: `${lang.toUpperCase()} WASM · izolováno`,
      filename: `solution.${lang}`,
    };
    if (runtimeLabel) runtimeLabel.textContent = info.runtime;
    if (filenameEl) filenameEl.textContent = info.filename;
    if (metaLine) {
      metaLine.textContent = `Jazyk: ${info.label} · Limit: 5 s · Síť: izolováno`;
    }
    if (langPicker) {
      langPicker.querySelectorAll("button[data-lang]").forEach((btn) => {
        btn.setAttribute("aria-pressed", btn.dataset.lang === lang ? "true" : "false");
      });
    }
  }

  function initLanguagePicker() {
    if (!langPicker || subjectLangs.length < 2) return;
    clearEl(langPicker);
    langPicker.hidden = false;
    subjectLangs.forEach((lang) => {
      const info = LANG_META[lang] || { label: lang.toUpperCase() };
      const btn = makeEl("button", "tab-pill", info.label);
      btn.type = "button";
      btn.dataset.lang = lang;
      btn.setAttribute("aria-pressed", lang === activeLang ? "true" : "false");
      btn.addEventListener("click", () => {
        if (activeLang === lang) return;
        activeLang = lang;
        if (currentTask) {
          renderTask(currentTask);
        } else {
          updateLanguageChrome(activeLang);
        }
      });
      langPicker.appendChild(btn);
    });
    updateLanguageChrome(activeLang);
  }
  initLanguagePicker();

  function switchOutputTab(tabName) {
    document.querySelectorAll("[data-output-tab]").forEach((btn) => {
      const active = btn.dataset.outputTab === tabName;
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll("[data-output-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.outputPanel !== tabName;
    });
  }

  function switchMobilePane(paneName) {
    document.querySelectorAll("[data-sandbox-pane-tab]").forEach((btn) => {
      const active = btn.dataset.sandboxPaneTab === paneName;
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll("[data-sandbox-pane]").forEach((pane) => {
      pane.dataset.mobileHidden = pane.dataset.sandboxPane === paneName ? "false" : "true";
    });
  }

  document.querySelectorAll("[data-output-tab]").forEach((btn) => {
    btn.addEventListener("click", () => switchOutputTab(btn.dataset.outputTab));
  });
  document.querySelectorAll("[data-sandbox-pane-tab]").forEach((btn) => {
    btn.addEventListener("click", () => switchMobilePane(btn.dataset.sandboxPaneTab));
  });

  function renderTask(task) {
    currentTask = task;
    if (taskTitle) taskTitle.textContent = task.title || "Programovací úloha v R";
    if (taskDesc) {
      const katexApi = window.UniSuite && window.UniSuite.katex;
      if (katexApi && typeof katexApi.renderRichText === "function") {
        katexApi.renderRichText(taskDesc, task.text || "");
      } else {
        taskDesc.textContent = task.text || "";
      }
    }
    if (taskFormula) {
      clearEl(taskFormula);
      if (task.formula) {
        taskFormula.hidden = false;
        const katexApi = window.UniSuite && window.UniSuite.katex;
        if (katexApi && typeof katexApi.renderMathNode === "function") {
          taskFormula.appendChild(katexApi.renderMathNode(task.formula, true));
        } else {
          taskFormula.textContent = task.formula;
        }
      } else {
        taskFormula.hidden = true;
      }
    }
    if (taskHints) {
      clearEl(taskHints);
      const hints = Array.isArray(task.hints) ? task.hints : [];
      if (hints.length > 0) {
        for (const h of hints) {
          taskHints.appendChild(makeEl("p", "", h));
        }
      } else {
        taskHints.appendChild(makeEl("p", "", "Pro tuto úlohu není uvedena nápověda."));
      }
    }
    const variant = resolveTaskVariant(task, activeLang);
    updateLanguageChrome(variant.language);
    editor.setLanguage(variant.language);
    editor.setStarterCode(variant.starter_code);
    editor.reset();
    renderTestsList(variant.visible_tests);
  }

  function renderTestsList(items) {
    clearEl(testsOut);
    if (!items || items.length === 0) {
      testsOut.appendChild(
        makeEl("p", "page-subtitle", "Spusť kód pomocí Ctrl+Enter nebo tlačítka Spustit testy."),
      );
      return;
    }
    const ul = makeEl("ul", "sbx-test-list");
    for (const item of items) {
      const li = makeEl("li", "sbx-test-row");
      const hasVerdict = typeof item.passed === "boolean";
      const badgeClass = !hasVerdict
        ? "sbx-badge"
        : item.passed
          ? "sbx-badge sbx-badge-pass"
          : "sbx-badge sbx-badge-fail";
      const badgeText = !hasVerdict ? "ČEKÁ" : item.passed ? "PASS" : "FAIL";
      li.appendChild(makeEl("span", badgeClass, badgeText));
      li.appendChild(makeEl("span", "sbx-test-name", item.name || "Test"));
      if (item.message) {
        li.appendChild(makeEl("span", "sbx-test-meta", item.message));
      }
      ul.appendChild(li);
    }
    testsOut.appendChild(ul);
  }

  async function runSandbox(withTests) {
    if (running) return;
    running = true;
    lastRunWithTests = withTests;
    if (runBtn) runBtn.disabled = true;
    if (testBtn) testBtn.disabled = true;
    if (mobileRunBtn) mobileRunBtn.disabled = true;

    const variant = resolveTaskVariant(currentTask, activeLang);
    const lang = variant.language;
    const tests = withTests && currentTask ? variant.visible_tests : [];

    try {
      const res = await defaultSandboxRegistry.run({
        language: lang,
        code: editor.getValue(),
        tests,
        plotTheme: plotViewer.plotTheme,
        timeoutMs: 5000,
      });

      const pre = consoleOut.querySelector("code");
      const combined = [res.stdout, res.stderr].filter(Boolean).join("\n").trim();
      if (pre) {
        pre.textContent = combined || `Dokončeno (${res.elapsed_ms} ms, stav: ${res.status}).`;
      }

      if (withTests) {
        renderTestsList(res.tests && res.tests.length ? res.tests : tests);
      }
      resultDiff.render(res.table_diff || null);
      await plotViewer.setPlots(res.plots || []);

      const plotsTab = document.querySelector('[data-output-tab="plots"]');
      if (plotsTab) {
        plotsTab.textContent = res.plots && res.plots.length ? `Grafy (${res.plots.length})` : "Grafy";
      }

      if (res.plots && res.plots.length && !withTests) {
        switchOutputTab("plots");
      } else if (withTests) {
        switchOutputTab("tests");
      } else {
        switchOutputTab("console");
      }
      switchMobilePane("output");
    } catch (err) {
      const pre = consoleOut.querySelector("code");
      if (pre) {
        pre.textContent = err && err.message ? err.message : "Chyba spuštění sandboxu.";
      }
      switchOutputTab("console");
      switchMobilePane("output");
    } finally {
      running = false;
      if (runBtn) runBtn.disabled = false;
      if (testBtn) testBtn.disabled = false;
      if (mobileRunBtn) mobileRunBtn.disabled = false;
    }
  }

  if (resetBtn) resetBtn.addEventListener("click", () => editor.reset());
  if (runBtn) runBtn.addEventListener("click", () => runSandbox(false));
  if (testBtn) testBtn.addEventListener("click", () => runSandbox(true));
  if (mobileRunBtn) mobileRunBtn.addEventListener("click", () => runSandbox(true));

  try {
    const resp = await fetch(
      `/api/${encodeURIComponent(state.subject.slug)}/questions?mode=practice&type=code_task&limit=50`,
      { credentials: "same-origin" },
    );
    if (resp.ok) {
      const data = await resp.json();
      tasks = Array.isArray(data.questions) ? data.questions : [];
    }
  } catch {
    tasks = [];
  }

  if (taskPicker && tasks.length > 0) {
    clearEl(taskPicker);
    tasks.forEach((task, idx) => {
      const btn = makeEl("button", "tab-pill", `${String(idx + 1).padStart(2, "0")} · ${task.title || task.id}`);
      btn.type = "button";
      btn.setAttribute("aria-pressed", idx === 0 ? "true" : "false");
      btn.addEventListener("click", () => {
        taskPicker.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
        btn.setAttribute("aria-pressed", "true");
        renderTask(task);
      });
      taskPicker.appendChild(btn);
    });
    renderTask(tasks[0]);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initStandaloneSandboxPage, { once: true });
} else {
  initStandaloneSandboxPage();
}
