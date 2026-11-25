/**
 * Choice (single & multi A/B/C/D) renderer + shared QuestionRendererRegistry bootstrap.
 * Semantic <fieldset>/<legend>/<label>/<input>, min 48px touch targets, 1-4 shortcuts.
 * Zero innerHTML, zero eval, zero inline style=.
 */
(() => {
  "use strict";

  const ns = (window.UniSuite = window.UniSuite || {});
  if (!ns.renderers) {
    const map = new Map();
    ns.renderers = {
      register(type, rendererFn) {
        map.set(String(type).toLowerCase(), rendererFn);
      },
      get(type) {
        return map.get(String(type || "choice").toLowerCase()) || null;
      },
      list() {
        return Array.from(map.keys());
      },
    };
  }

  function renderChoice(question, context = {}) {
    const dom = ns.dom;
    const katex = ns.katex;
    const isMulti = Boolean(question && question.multi);
    const options =
      question && question.options && typeof question.options === "object"
        ? question.options
        : {};
    const keys = Object.keys(options);
    const groupName = `q_${String((question && question.id) || "choice").replace(/[^a-zA-Z0-9_-]/g, "_")}_${String(context.stepIndex ?? "main")}`;

    const fieldset = dom.el("fieldset", { className: "eng-choice-fieldset" });
    fieldset.appendChild(
      dom.el(
        "legend",
        { className: "eng-choice-legend" },
        isMulti
          ? "Vyber všechny správné možnosti (klávesy 1–4):"
          : "Vyber jednu správnou možnost (klávesy 1–4):",
      ),
    );

    const inputs = [];
    const optionLabels = new Map();

    keys.forEach((optKey, idx) => {
      const inputId = `${groupName}_${optKey}`;
      const input = dom.el("input", {
        type: isMulti ? "checkbox" : "radio",
        id: inputId,
        name: groupName,
        value: optKey,
        className: "eng-choice-input",
      });
      const textSpan = dom.el("span", { className: "eng-choice-label" });
      if (katex && typeof katex.renderRichText === "function") {
        katex.renderRichText(textSpan, String(options[optKey] ?? ""));
      } else {
        textSpan.textContent = String(options[optKey] ?? "");
      }

      const label = dom.el(
        "label",
        {
          className: "eng-choice-option",
          for: inputId,
          dataset: { optionKey: optKey, hotkey: String(idx + 1) },
        },
        input,
        dom.el("span", { className: "eng-choice-key", "aria-hidden": "true" }, optKey),
        textSpan,
      );

      input.addEventListener("change", () => {
        if (typeof context.onAnswerChange === "function") {
          context.onAnswerChange();
        }
      });

      inputs.push(input);
      optionLabels.set(optKey.toUpperCase(), label);
      fieldset.appendChild(label);
    });

    function getAnswer() {
      const checked = inputs.filter((i) => i.checked).map((i) => i.value);
      return isMulti ? checked : checked[0] || "";
    }

    function hasAnswer() {
      return inputs.some((i) => i.checked);
    }

    function setLocked(locked) {
      for (const input of inputs) {
        input.disabled = Boolean(locked);
      }
    }

    function showResult(result) {
      const expected = new Set(
        ((result && result.details && result.details.expected) || []).map((k) =>
          String(k).toUpperCase(),
        ),
      );
      for (const input of inputs) {
        const key = input.value.toUpperCase();
        const lbl = optionLabels.get(key);
        if (!lbl) continue;
        if (expected.has(key)) {
          lbl.dataset.state = "correct";
        } else if (input.checked) {
          lbl.dataset.state = "wrong";
        }
      }
    }

    function handleKey(event) {
      const idx = Number.parseInt(event.key, 10) - 1;
      if (!Number.isInteger(idx) || idx < 0 || idx >= inputs.length) return false;
      const target = inputs[idx];
      if (!target || target.disabled) return false;
      if (isMulti) {
        target.checked = !target.checked;
      } else {
        target.checked = true;
      }
      target.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }

    return {
      element: fieldset,
      getAnswer,
      hasAnswer,
      focus: () => {
        if (inputs[0]) inputs[0].focus();
      },
      setLocked,
      showResult,
      handleKey,
    };
  }

  ns.renderers.register("choice", renderChoice);
})();
