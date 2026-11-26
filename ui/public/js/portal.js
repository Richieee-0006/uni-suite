(() => {
  "use strict";

  function wireDialogs() {
    for (const btn of document.querySelectorAll("[data-dialog-close]")) {
      btn.addEventListener("click", () => {
        const dialog = btn.closest("dialog");
        if (dialog && dialog.open) {
          dialog.close();
        }
      });
    }

    for (const dialog of document.querySelectorAll("dialog")) {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog && dialog.open) {
          dialog.close();
        }
      });
    }
  }

  function wireFocusToggle() {
    for (const btn of document.querySelectorAll("[data-toggle-focus]")) {
      btn.addEventListener("click", () => {
        const cust = window.UniSuite && window.UniSuite.customizer;
        if (!cust) return;
        const cur = cust.get();
        const next = !cur.focus_mode;
        cust.updateKey("focus_mode", next);
        if (window.UniSuite.dom) {
          window.UniSuite.dom.announce(next ? "Focus mód zapnut" : "Focus mód vypnut");
        }
      });
    }
  }

  function wireSubjectControls() {
    const topicButtons = document.querySelectorAll("[data-topic-select]");
    const ctaSub = document.getElementById("subject-cta-sub");

    for (const btn of topicButtons) {
      btn.addEventListener("click", () => {
        for (const other of topicButtons) {
          other.setAttribute("aria-pressed", other === btn ? "true" : "false");
        }
        if (ctaSub && btn.dataset.topicLabel) {
          ctaSub.textContent = btn.dataset.topicLabel;
        }
      });
    }

    const filterButtons = document.querySelectorAll("[data-filter-type]");
    for (const btn of filterButtons) {
      btn.addEventListener("click", () => {
        for (const other of filterButtons) {
          other.setAttribute("aria-pressed", other === btn ? "true" : "false");
        }
        btn.scrollIntoView({ inline: "nearest", block: "nearest" });
      });
    }

    const mobileTabs = document.querySelectorAll("[data-sandbox-pane-tab]");
    const panes = document.querySelectorAll("[data-sandbox-pane]");
    for (const tab of mobileTabs) {
      tab.addEventListener("click", () => {
        const target = tab.dataset.sandboxPaneTab;
        for (const t of mobileTabs) {
          t.setAttribute("aria-selected", t === tab ? "true" : "false");
        }
        for (const pane of panes) {
          pane.dataset.mobileHidden = pane.dataset.sandboxPane === target ? "false" : "true";
        }
        tab.scrollIntoView({ inline: "nearest", block: "nearest" });
      });
    }

    const outTabs = document.querySelectorAll("[data-output-tab]");
    const outPanels = document.querySelectorAll("[data-output-panel]");
    for (const tab of outTabs) {
      tab.addEventListener("click", () => {
        const target = tab.dataset.outputTab;
        for (const t of outTabs) {
          t.setAttribute("aria-selected", t === tab ? "true" : "false");
        }
        for (const panel of outPanels) {
          panel.hidden = panel.dataset.outputPanel !== target;
        }
        tab.scrollIntoView({ inline: "nearest", block: "nearest" });
      });
    }

    const resetBtn = document.getElementById("sandbox-reset-btn");
    const codeArea = document.getElementById("code-textarea");
    if (resetBtn && codeArea) {
      const initialCode = codeArea.value;
      resetBtn.addEventListener("click", () => {
        codeArea.value = initialCode;
        if (window.UniSuite && window.UniSuite.dom) {
          window.UniSuite.dom.announce("Kód byl obnoven do výchozího stavu");
        }
      });
    }
  }

  function bootstrap() {
    const ns = window.UniSuite || {};
    if (typeof ns.initCustomizer === "function") ns.initCustomizer();
    if (typeof ns.initNavDrawer === "function") ns.initNavDrawer();
    if (typeof ns.initCmdPalette === "function") ns.initCmdPalette();
    if (typeof ns.initScratchpad === "function") ns.initScratchpad();
    if (typeof ns.initFeedback === "function") ns.initFeedback();
    wireDialogs();
    wireFocusToggle();
    wireSubjectControls();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
  } else {
    bootstrap();
  }
})();
