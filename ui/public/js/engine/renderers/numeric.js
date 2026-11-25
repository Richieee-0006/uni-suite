/**
 * Tolerance-aware numeric question renderer.
 * Supports inputmode="decimal", '.', ',', fractions ('17/12'), and scientific ('1.5e-4').
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

  function renderNumeric(question, context = {}) {
    const dom = ns.dom;
    const inputId = `num_${String((question && question.id) || "q").replace(/[^a-zA-Z0-9_-]/g, "_")}_${String(context.stepIndex ?? "main")}`;
    const unit = question && question.unit ? String(question.unit) : "";
    const tol = question && question.tolerance !== undefined ? question.tolerance : null;

    const input = dom.el("input", {
      type: "text",
      id: inputId,
      className: "eng-text-input",
      inputmode: "decimal",
      autocomplete: "off",
      autocapitalize: "off",
      spellcheck: "false",
      placeholder: "Např. 3,14 · 17/12 · 1.5e-4",
    });

    input.addEventListener("input", () => {
      if (typeof context.onAnswerChange === "function") context.onAnswerChange();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && typeof context.onSubmit === "function") {
        event.preventDefault();
        context.onSubmit();
      }
    });

    const row = dom.el(
      "div",
      { className: "eng-input-row" },
      input,
      unit ? dom.el("span", { className: "eng-input-unit" }, unit) : null,
    );

    const helpText =
      tol !== null
        ? `Podporuje desetinnou čárku i tečku, zlomky (17/12) i vědecký zápis (1.5e-4). Tolerance ε = ${tol}.`
        : "Podporuje desetinnou čárku i tečku, zlomky (17/12) i vědecký zápis (1.5e-4).";

    const box = dom.el(
      "div",
      { className: "eng-input-group" },
      dom.el("label", { className: "eng-input-label", for: inputId }, "Číselný výsledek"),
      row,
      dom.el("p", { className: "eng-input-help" }, helpText),
    );

    return {
      element: box,
      getAnswer: () => input.value.trim(),
      hasAnswer: () => input.value.trim().length > 0,
      focus: () => input.focus(),
      setLocked: (locked) => {
        input.disabled = Boolean(locked);
      },
      showResult: () => {},
    };
  }

  ns.renderers.register("input_numeric", renderNumeric);
})();
