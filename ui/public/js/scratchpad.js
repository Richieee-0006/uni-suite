(() => {
  "use strict";

  function initScratchpad() {
    const dom = window.UniSuite && window.UniSuite.dom;
    const refDialog = document.getElementById("reference-dialog");
    const scratchDialog = document.getElementById("scratchpad-dialog");
    const textarea = document.getElementById("scratchpad-input");
    const metaOut = document.getElementById("scratchpad-meta");
    const clearBtn = document.getElementById("scratchpad-clear");
    const refList = document.getElementById("reference-list");

    const state = dom ? dom.readState() : {};
    const scopeSlug = state.subject && state.subject.slug ? state.subject.slug : "portal";
    const storageKey = `uni_scratchpad_${scopeSlug}`;

    function updateMeta() {
      if (!textarea || !metaOut) return;
      const len = textarea.value.length;
      metaOut.textContent = len === 0 ? "Prázdný" : `${len} znaků (relace)`;
    }

    if (textarea) {
      try {
        const saved = window.sessionStorage.getItem(storageKey);
        if (typeof saved === "string") {
          textarea.value = saved;
        }
      } catch {
        // Ignore sessionStorage restrictions.
      }
      updateMeta();

      textarea.addEventListener("input", () => {
        try {
          window.sessionStorage.setItem(storageKey, textarea.value);
        } catch {
          // Ignore quota errors.
        }
        updateMeta();
      });
    }

    if (clearBtn && textarea) {
      clearBtn.addEventListener("click", () => {
        textarea.value = "";
        try {
          window.sessionStorage.removeItem(storageKey);
        } catch {
          // Ignore errors.
        }
        updateMeta();
        textarea.focus();
        if (dom) dom.announce("Škrabák byl vymazán");
      });
    }

    if (refList && dom && Array.isArray(state.formulas) && state.formulas.length > 0) {
      dom.clear(refList);
      for (const item of state.formulas) {
        if (!item || typeof item !== "object") continue;
        refList.appendChild(
          dom.el(
            "article",
            { className: "ref-card" },
            dom.el("h3", { className: "ref-name" }, item.title || item.name || "Vzorec"),
            item.formula ? dom.el("p", { className: "ref-expr" }, item.formula) : null,
            item.note ? dom.el("p", { className: "ref-note" }, item.note) : null
          )
        );
      }
    }

    function openReference() {
      if (refDialog && !refDialog.open) {
        refDialog.showModal();
      }
    }

    function openScratchpad() {
      if (scratchDialog && !scratchDialog.open) {
        scratchDialog.showModal();
        if (textarea) {
          window.requestAnimationFrame(() => textarea.focus());
        }
      }
    }

    for (const btn of document.querySelectorAll("[data-open-reference]")) {
      btn.addEventListener("click", openReference);
    }

    for (const btn of document.querySelectorAll("[data-open-scratchpad]")) {
      btn.addEventListener("click", openScratchpad);
    }

    document.addEventListener("keydown", (event) => {
      if (
        !dom ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        document.documentElement.dataset.shortcuts === "0" ||
        dom.isEditableTarget(event.target)
      ) {
        return;
      }

      const openModal = document.querySelector("dialog[open]");
      const key = event.key.toLowerCase();

      if (key === "f") {
        if (refDialog && openModal === refDialog) {
          event.preventDefault();
          refDialog.close();
        } else if (!openModal) {
          event.preventDefault();
          openReference();
        }
      } else if (key === "s") {
        if (scratchDialog && openModal === scratchDialog) {
          event.preventDefault();
          scratchDialog.close();
        } else if (!openModal) {
          event.preventDefault();
          openScratchpad();
        }
      }
    });

    window.UniSuite = window.UniSuite || {};
    window.UniSuite.scratchpad = {
      openReference,
      openScratchpad,
    };
  }

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.initScratchpad = initScratchpad;
})();
