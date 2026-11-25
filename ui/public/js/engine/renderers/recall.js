/**
 * Recall (doplňovačka) question renderer with Czech Levenshtein <= 1 server tolerance.
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

  function renderRecall(question, context = {}) {
    const dom = ns.dom;
    const inputId = `recall_${String((question && question.id) || "q").replace(/[^a-zA-Z0-9_-]/g, "_")}_${String(context.stepIndex ?? "main")}`;
    const wordsHint =
      question && Number.isInteger(question.words) && question.words > 0
        ? `Počet slov: ${question.words} · tolerance 1 překlepu a bez diakritiky.`
        : "Tolerance 1 překlepu (Levenshtein ≤ 1), velikost písmen a diakritika se neřeší.";

    const input = dom.el("input", {
      type: "text",
      id: inputId,
      className: "eng-text-input",
      autocomplete: "off",
      autocapitalize: "off",
      spellcheck: "false",
      placeholder: "Napiš pojem nebo definici",
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

    const box = dom.el(
      "div",
      { className: "eng-input-group" },
      question && question.category
        ? dom.el("span", { className: "eng-mode-pill" }, String(question.category))
        : null,
      dom.el("label", { className: "eng-input-label", for: inputId }, "Doplň pojem"),
      dom.el("div", { className: "eng-input-row" }, input),
      dom.el("p", { className: "eng-input-help" }, wordsHint),
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

  ns.renderers.register("recall", renderRecall);
})();
