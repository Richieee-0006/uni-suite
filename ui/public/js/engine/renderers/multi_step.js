/**
 * Multi-step question renderer composing registered step renderers declaratively.
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

  function renderMultiStep(question, context = {}) {
    const dom = ns.dom;
    const katex = ns.katex;
    const steps = Array.isArray(question && question.steps) ? question.steps : [];
    const wrap = dom.el("div", { className: "eng-steps" });

    const subInstances = [];
    const stepCards = [];

    steps.forEach((step, idx) => {
      const stepType = String((step && step.type) || "input_numeric").toLowerCase();
      const factory = ns.renderers.get(stepType) || ns.renderers.get("input_exact");
      const promptEl = dom.el("div", { className: "eng-step-title" });
      if (katex && typeof katex.renderRichText === "function") {
        katex.renderRichText(promptEl, String((step && step.prompt) || `Krok ${idx + 1}`));
      } else {
        promptEl.textContent = String((step && step.prompt) || `Krok ${idx + 1}`);
      }

      const stepQ = {
        ...step,
        id: `${String((question && question.id) || "ms")}_s${idx + 1}`,
      };
      const sub = factory
        ? factory(stepQ, {
            stepIndex: idx,
            onAnswerChange: context.onAnswerChange,
            onSubmit: idx === steps.length - 1 ? context.onSubmit : undefined,
          })
        : null;

      const card = dom.el(
        "section",
        {
          className: "eng-step-card",
          "aria-label": `Krok ${idx + 1}`,
        },
        promptEl,
        sub ? sub.element : null,
      );

      stepCards.push(card);
      if (sub) subInstances.push(sub);
      wrap.appendChild(card);
    });

    return {
      element: wrap,
      getAnswer: () => subInstances.map((s) => s.getAnswer()),
      hasAnswer: () => subInstances.length > 0 && subInstances.every((s) => s.hasAnswer()),
      focus: () => {
        if (subInstances[0]) subInstances[0].focus();
      },
      setLocked: (locked) => {
        for (const sub of subInstances) {
          sub.setLocked(locked);
        }
      },
      showResult: (result) => {
        const stepRes = (result && result.details && result.details.steps) || [];
        stepRes.forEach((item, idx) => {
          if (stepCards[idx]) {
            stepCards[idx].dataset.state = item.correct ? "correct" : "wrong";
          }
          if (subInstances[idx] && typeof subInstances[idx].showResult === "function") {
            subInstances[idx].showResult({ details: item.details || {} });
          }
        });
      },
    };
  }

  ns.renderers.register("multi_step", renderMultiStep);
})();
