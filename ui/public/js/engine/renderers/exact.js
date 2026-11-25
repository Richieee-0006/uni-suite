/**
 * Exact text / base-conversion / TZI v1 reveal question renderer.
 * Uses autocapitalize="off" spellcheck="false" and 16px input to avoid iOS zoom.
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

  function renderExact(question, context = {}) {
    const dom = ns.dom;
    const isReveal = Boolean(question && question.reveal);
    const inputId = `exact_${String((question && question.id) || "q").replace(/[^a-zA-Z0-9_-]/g, "_")}_${String(context.stepIndex ?? "main")}`;

    if (isReveal) {
      let selfVerdict = null;
      const okBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-secondary btn-sm", "aria-pressed": "false" },
        "Věděl(a) jsem postup",
      );
      const errBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-secondary btn-sm", "aria-pressed": "false" },
        "Potřebuji procvičit",
      );

      function pick(val) {
        selfVerdict = val;
        okBtn.setAttribute("aria-pressed", val ? "true" : "false");
        errBtn.setAttribute("aria-pressed", val ? "false" : "true");
        if (typeof context.onAnswerChange === "function") context.onAnswerChange();
      }

      okBtn.addEventListener("click", () => pick(true));
      errBtn.addEventListener("click", () => pick(false));

      const box = dom.el(
        "div",
        { className: "eng-input-group" },
        dom.el(
          "p",
          { className: "eng-input-help" },
          "Důkaz / otevřená úloha: promysli si postup a ohodnoť se před zobrazením řešení.",
        ),
        dom.el("div", { className: "eng-code-actions" }, okBtn, errBtn),
      );

      return {
        element: box,
        getAnswer: () => ({ self_correct: Boolean(selfVerdict) }),
        hasAnswer: () => selfVerdict !== null,
        focus: () => okBtn.focus(),
        setLocked: (locked) => {
          okBtn.disabled = Boolean(locked);
          errBtn.disabled = Boolean(locked);
        },
        showResult: () => {},
      };
    }

    const input = dom.el("input", {
      type: "text",
      id: inputId,
      className: "eng-text-input",
      autocomplete: "off",
      autocapitalize: "off",
      spellcheck: "false",
      placeholder:
        question && typeof question.hint === "string" && !question.hints_locked
          ? question.hint
          : "Zadej přesnou odpověď",
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
      dom.el("label", { className: "eng-input-label", for: inputId }, "Odpověď"),
      dom.el("div", { className: "eng-input-row" }, input),
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

  ns.renderers.register("input_exact", renderExact);
  ns.renderers.register("input", renderExact);
})();
