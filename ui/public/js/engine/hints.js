/**
 * 3-step progressive hint reveal controller (auto-locked in exam & sudden_death).
 * Strict Trusted Types compliant (zero innerHTML, zero inline style=).
 */
(() => {
  "use strict";

  const LOCKED_MODES = new Set(["exam", "sudden_death"]);

  function createHintController({ slug, question, mode, onChange }) {
    const dom = window.UniSuite && window.UniSuite.dom;
    const katex = window.UniSuite && window.UniSuite.katex;
    const isLocked =
      LOCKED_MODES.has(String(mode || "").toLowerCase()) ||
      Boolean(question && question.hints_locked);
    const totalSteps = isLocked
      ? 0
      : Math.min(3, Number((question && question.hints_count) || (question && Array.isArray(question.hints) ? question.hints.length : 0)));

    let revealedCount = 0;
    const revealedTexts = [];
    let busy = false;

    const root = dom ? dom.el("div", { className: "eng-hints-box" }) : document.createElement("div");
    const listEl = dom ? dom.el("div", { className: "eng-hints-list" }) : document.createElement("div");
    const buttonEl = dom
      ? dom.el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          disabled: isLocked || totalSteps === 0,
        })
      : document.createElement("button");

    function updateButtonLabel() {
      if (isLocked) {
        buttonEl.textContent = "Nápovědy zamčeny";
        buttonEl.disabled = true;
        return;
      }
      if (totalSteps === 0) {
        buttonEl.textContent = "Bez nápověd";
        buttonEl.disabled = true;
        return;
      }
      if (revealedCount >= totalSteps) {
        buttonEl.textContent = `Všechny nápovědy (${revealedCount}/${totalSteps})`;
        buttonEl.disabled = true;
        return;
      }
      buttonEl.textContent = `Nápověda (${revealedCount + 1}/${totalSteps})`;
      buttonEl.disabled = busy;
    }

    function appendHintItem(text, stepNum) {
      const item = dom
        ? dom.el("div", {
            className: "eng-hint-item",
            dataset: { hintStep: String(stepNum) },
          })
        : document.createElement("div");
      if (katex && typeof katex.renderRichText === "function") {
        katex.renderRichText(item, text);
      } else {
        item.textContent = text;
      }
      listEl.appendChild(item);
    }

    async function revealNext() {
      if (isLocked || busy || revealedCount >= totalSteps) return;
      const nextStep = revealedCount + 1;
      busy = true;
      updateButtonLabel();

      try {
        let hintText = null;
        if (question && Array.isArray(question.hints) && question.hints[nextStep - 1]) {
          hintText = String(question.hints[nextStep - 1]);
        } else if (dom && slug && question && question.id) {
          const qs = new URLSearchParams({
            step: String(nextStep),
            mode: String(mode || "practice"),
          });
          if (question.generator && question.generator.id) {
            qs.set("generator_id", String(question.generator.id));
            qs.set("seed", String(question.generator.seed));
          }
          const resp = await dom.apiFetch(
            `/api/${encodeURIComponent(slug)}/hint/${encodeURIComponent(question.id)}?${qs.toString()}`,
          );
          if (resp && resp.hint) {
            hintText = String(resp.hint);
          }
        }

        if (hintText) {
          revealedCount = nextStep;
          revealedTexts.push(hintText);
          appendHintItem(hintText, nextStep);
          if (dom) {
            dom.announce(`Zobrazena nápověda ${nextStep} z ${totalSteps}`);
          }
          if (typeof onChange === "function") {
            onChange({ step: revealedCount, total: totalSteps });
          }
        }
      } catch (err) {
        if (dom) {
          dom.announce(err && err.message ? err.message : "Nápověda není dostupná", true);
        }
      } finally {
        busy = false;
        updateButtonLabel();
      }
    }

    buttonEl.addEventListener("click", revealNext);
    updateButtonLabel();

    if (isLocked) {
      root.appendChild(
        dom
          ? dom.el(
              "p",
              { className: "eng-hints-locked" },
              "V režimu zkoušky a náhlé smrti jsou nápovědy zamčeny na serveru.",
            )
          : document.createElement("p"),
      );
    } else if (totalSteps > 0) {
      root.appendChild(listEl);
    }

    return {
      element: root,
      triggerButton: buttonEl,
      isLocked,
      totalSteps,
      getRevealedCount: () => revealedCount,
      revealNext,
    };
  }

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.hints = {
    createHintController,
    LOCKED_MODES,
  };
})();
