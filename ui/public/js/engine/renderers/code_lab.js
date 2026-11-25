/**
 * Code task renderer delegating execution to window.UniSandbox driver registry
 * (with graceful fallback to /api/sandbox/run when Tier-1 worker isn't mounted).
 */
(() => {
  "use strict";

  const ns = (window.UniSuite = window.UniSuite || {});
  if (!ns.renderers) {
    const map = new Map();
    ns.renderers = {
      register(type, fn) {
        map.set(String(type).toLowerCase(), fn);
      },
      get(type) {
        return map.get(String(type || "choice").toLowerCase()) || null;
      },
      list() {
        return Array.from(map.keys());
      },
    };
  }

  async function executeInSandbox(language, code, tests) {
    const sandbox = window.UniSandbox || ns.sandbox;
    if (sandbox && typeof sandbox.run === "function") {
      return sandbox.run({ language, code, tests });
    }
    if (
      sandbox &&
      sandbox.defaultSandboxRegistry &&
      typeof sandbox.defaultSandboxRegistry.run === "function"
    ) {
      return sandbox.defaultSandboxRegistry.run({ language, code, tests });
    }
    if (ns.dom && typeof ns.dom.apiFetch === "function") {
      return ns.dom.apiFetch("/api/sandbox/run", {
        method: "POST",
        body: JSON.stringify({ language, code, tests }),
      });
    }
    throw new Error("Code Sandbox není na této stránce dostupný.");
  }

  function renderCodeLab(question, context = {}) {
    const dom = ns.dom;
    const katex = ns.katex;
    const lang = String((question && question.language) || "r").toLowerCase();
    const starter = String((question && question.starter_code) || "");
    const visibleTests = Array.isArray(question && question.visible_tests)
      ? question.visible_tests
      : [];
    const editorId = `code_${String((question && question.id) || "task").replace(/[^a-zA-Z0-9_-]/g, "_")}`;

    let lastRunResult = null;
    let running = false;

    const textarea = dom.el("textarea", {
      id: editorId,
      className: "eng-code-textarea",
      rows: "10",
      spellcheck: "false",
      autocapitalize: "off",
      lang: "en",
    });
    textarea.value = starter;

    const statusOut = dom.el("output", { className: "eng-feedback-meta" }, "Připraveno");
    const testsList = dom.el("div", { className: "eng-code-tests" });
    const runBtn = dom.el(
      "button",
      { type: "button", className: "btn btn-secondary btn-sm" },
      "Spustit testy (Ctrl+Enter)",
    );
    const resetBtn = dom.el(
      "button",
      { type: "button", className: "btn btn-ghost btn-sm" },
      "Reset kódu",
    );

    function renderTestRows(results) {
      dom.clear(testsList);
      const list = Array.isArray(results) && results.length > 0 ? results : visibleTests;
      for (const item of list) {
        const hasVerdict = typeof item.passed === "boolean";
        const badge = hasVerdict ? (item.passed ? "PASS" : "FAIL") : "Čeká";
        testsList.appendChild(
          dom.el(
            "div",
            {
              className: "eng-test-row",
              dataset: hasVerdict ? { passed: item.passed ? "1" : "0" } : {},
            },
            dom.el("span", {}, String(item.name || "Test")),
            dom.el("span", { className: "mono" }, badge),
          ),
        );
      }
    }

    async function runTests() {
      if (running) return;
      running = true;
      runBtn.disabled = true;
      statusOut.textContent = "Spouštím sandbox...";
      try {
        const res = await executeInSandbox(lang, textarea.value, visibleTests);
        lastRunResult = {
          ok: Boolean(res && res.ok),
          language: lang,
          driver: String((res && res.driver) || "webr"),
          tier: String((res && res.tier) || "tier1_wasm"),
          tests: Array.isArray(res && res.tests) ? res.tests : [],
        };
        renderTestRows(lastRunResult.tests);
        const passed = lastRunResult.tests.filter((t) => t.passed).length;
        statusOut.textContent = `Výsledek: ${passed}/${lastRunResult.tests.length} testů PASS (${lastRunResult.driver})`;
        dom.announce(statusOut.textContent);
        if (typeof context.onAnswerChange === "function") {
          context.onAnswerChange();
        }
      } catch (err) {
        statusOut.textContent = err && err.message ? err.message : "Chyba spuštění sandboxu";
        dom.announce(statusOut.textContent, true);
      } finally {
        running = false;
        runBtn.disabled = false;
      }
    }

    runBtn.addEventListener("click", runTests);
    resetBtn.addEventListener("click", () => {
      textarea.value = starter;
      lastRunResult = null;
      renderTestRows(visibleTests);
      statusOut.textContent = "Kód obnoven";
      dom.announce("Kód obnoven do výchozího stavu");
    });

    textarea.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        runTests();
      }
    });

    const box = dom.el("div", { className: "eng-code-box" });
    if (question && question.formula && katex && typeof katex.renderMathNode === "function") {
      box.appendChild(katex.renderMathNode(question.formula, true));
    }
    box.appendChild(
      dom.el("label", { className: "eng-input-label", for: editorId }, `Kód (${lang.toUpperCase()})`),
    );
    box.appendChild(textarea);
    box.appendChild(dom.el("div", { className: "eng-code-actions" }, runBtn, resetBtn, statusOut));
    box.appendChild(testsList);

    renderTestRows(visibleTests);

    return {
      element: box,
      getAnswer: () => lastRunResult,
      hasAnswer: () => lastRunResult !== null,
      focus: () => textarea.focus(),
      setLocked: (locked) => {
        textarea.disabled = Boolean(locked);
        runBtn.disabled = Boolean(locked);
        resetBtn.disabled = Boolean(locked);
      },
      showResult: () => {},
    };
  }

  ns.renderers.register("code_task", renderCodeLab);
})();
