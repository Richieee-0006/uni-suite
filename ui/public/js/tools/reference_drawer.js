/**
 * Reference Drawer (F Tahák <dialog>) hydrator.
 * Loads subject formulas (KaTeX MathML), quantile tables (PAS), and searchable glossary
 * without leaving the active question. Zero innerHTML, zero inline style=.
 */
(() => {
  "use strict";

  const ns = (window.UniSuite = window.UniSuite || {});

  function normalizeText(str) {
    return String(str || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  function initReferenceDrawer() {
    const dom = ns.dom;
    const katex = ns.katex;
    const refDialog = document.getElementById("reference-dialog");
    const refList = document.getElementById("reference-list");
    if (!dom || !refDialog || !refList) return;

    const state = dom.readState();
    const slug = state && state.subject && state.subject.slug ? state.subject.slug : "";
    if (!slug) return;

    let loaded = false;
    let activeTab = "formulas";
    let searchQuery = "";
    const cache = {
      formulas: null,
      glossary: null,
      dist: null,
    };

    const tabsBar = dom.el("div", {
      className: "ref-tabs",
      role: "tablist",
      "aria-label": "Sekce taháku",
    });
    const searchInput = dom.el("input", {
      type: "search",
      className: "ref-search-input",
      placeholder: "Filtrovat vzorce nebo pojmy...",
      "aria-label": "Filtrovat obsah taháku",
      autocomplete: "off",
    });
    const panelHost = dom.el("div", { className: "ref-list" });

    const tabDefs = [
      { id: "formulas", label: "Vzorce" },
      { id: "glossary", label: "Glosář" },
      { id: "tables", label: "Kvantily" },
    ];
    const tabButtons = new Map();

    for (const def of tabDefs) {
      const btn = dom.el(
        "button",
        {
          type: "button",
          className: "ref-tab-btn",
          role: "tab",
          "aria-selected": def.id === activeTab ? "true" : "false",
        },
        def.label,
      );
      btn.addEventListener("click", () => {
        activeTab = def.id;
        for (const [id, b] of tabButtons.entries()) {
          b.setAttribute("aria-selected", id === activeTab ? "true" : "false");
        }
        renderActiveTab();
      });
      tabButtons.set(def.id, btn);
      tabsBar.appendChild(btn);
    }

    searchInput.addEventListener("input", () => {
      searchQuery = normalizeText(searchInput.value.trim());
      renderActiveTab();
    });

    function renderFormulasTab() {
      dom.clear(panelHost);
      const sections = (cache.formulas && cache.formulas.sections) || [];
      let count = 0;
      for (const sec of sections) {
        const matched = (sec.items || []).filter((item) => {
          if (!searchQuery) return true;
          return (
            normalizeText(item.name).includes(searchQuery) ||
            normalizeText(item.note).includes(searchQuery) ||
            normalizeText(sec.title).includes(searchQuery)
          );
        });
        if (matched.length === 0) continue;
        panelHost.appendChild(dom.el("h3", { className: "ref-section-title" }, sec.title));
        for (const item of matched) {
          count += 1;
          const exprWrap = dom.el("div", { className: "ref-expr" });
          if (item.latex && katex && typeof katex.renderMathNode === "function") {
            exprWrap.appendChild(katex.renderMathNode(item.latex, true));
          } else {
            exprWrap.textContent = item.latex || "";
          }
          const noteEl = dom.el("p", { className: "ref-note" });
          if (item.note && katex && typeof katex.renderRichText === "function") {
            katex.renderRichText(noteEl, item.note);
          } else {
            noteEl.textContent = item.note || "";
          }
          panelHost.appendChild(
            dom.el(
              "article",
              { className: "ref-card" },
              dom.el("h4", { className: "ref-name" }, item.name),
              exprWrap,
              item.note ? noteEl : null,
            ),
          );
        }
      }
      if (count === 0) {
        panelHost.appendChild(
          dom.el("p", { className: "page-subtitle" }, "Žádné odpovídající vzorce."),
        );
      }
    }

    function renderGlossaryTab() {
      dom.clear(panelHost);
      const items = (cache.glossary && cache.glossary.items) || [];
      const matched = items.filter((item) => {
        if (!searchQuery) return true;
        return (
          normalizeText(item.term).includes(searchQuery) ||
          normalizeText(item.body).includes(searchQuery)
        );
      });
      if (matched.length === 0) {
        panelHost.appendChild(
          dom.el("p", { className: "page-subtitle" }, "Žádné odpovídající pojmy v glosáři."),
        );
        return;
      }
      for (const item of matched.slice(0, 60)) {
        const bodyEl = dom.el("p", { className: "ref-note" });
        if (katex && typeof katex.renderRichText === "function") {
          katex.renderRichText(bodyEl, item.body);
        } else {
          bodyEl.textContent = item.body;
        }
        panelHost.appendChild(
          dom.el(
            "article",
            { className: "ref-card" },
            dom.el("h4", { className: "ref-name" }, item.term),
            bodyEl,
          ),
        );
      }
    }

    function renderTablesTab() {
      dom.clear(panelHost);
      const tables = cache.dist && cache.dist.tables;
      if (!tables) {
        panelHost.appendChild(
          dom.el("p", { className: "page-subtitle" }, "Kvantilové tabulky nejsou načteny."),
        );
        return;
      }

      const normRows = (tables.normal || []).map((row) =>
        dom.el(
          "tr",
          {},
          dom.el("td", {}, String(row.p)),
          dom.el("td", {}, String(row.q)),
        ),
      );
      panelHost.appendChild(
        dom.el("h3", { className: "ref-section-title" }, "Normované normální rozdělení N(0, 1)"),
      );
      panelHost.appendChild(
        dom.el(
          "div",
          { className: "lab-table-wrap" },
          dom.el(
            "table",
            { className: "lab-table" },
            dom.el(
              "thead",
              {},
              dom.el(
                "tr",
                {},
                dom.el("th", { scope: "col" }, "p"),
                dom.el("th", { scope: "col" }, "u_p"),
              ),
            ),
            dom.el("tbody", {}, normRows),
          ),
        ),
      );

      const tProbs = tables.t_probs || [];
      const tHeadCells = [
        dom.el("th", { scope: "col" }, "df"),
        ...tProbs.map((p) => dom.el("th", { scope: "col" }, String(p))),
      ];
      const tBodyRows = (tables.t || []).map((row) =>
        dom.el(
          "tr",
          {},
          dom.el("td", {}, String(row.df)),
          ...tProbs.map((p) => dom.el("td", {}, String((row.values && row.values[String(p)]) || ""))),
        ),
      );
      panelHost.appendChild(
        dom.el("h3", { className: "ref-section-title" }, "Studentovo t-rozdělení"),
      );
      panelHost.appendChild(
        dom.el(
          "div",
          { className: "lab-table-wrap" },
          dom.el(
            "table",
            { className: "lab-table" },
            dom.el("thead", {}, dom.el("tr", {}, tHeadCells)),
            dom.el("tbody", {}, tBodyRows),
          ),
        ),
      );
    }

    function renderActiveTab() {
      searchInput.hidden = activeTab === "tables";
      if (activeTab === "formulas") renderFormulasTab();
      else if (activeTab === "glossary") renderGlossaryTab();
      else renderTablesTab();
    }

    async function hydrate() {
      if (loaded) return;
      loaded = true;
      dom.clear(refList);
      refList.appendChild(tabsBar);
      refList.appendChild(searchInput);
      refList.appendChild(panelHost);
      panelHost.appendChild(dom.el("p", { className: "page-subtitle" }, "Načítám tahák..."));

      const encSlug = encodeURIComponent(slug);
      const [formulasRes, glossaryRes, distRes] = await Promise.allSettled([
        dom.apiFetch(`/api/${encSlug}/tools/formulas`),
        dom.apiFetch(`/api/${encSlug}/tools/glossary`),
        dom.apiFetch(`/api/${encSlug}/tools/dist_tables?include_tables=true`),
      ]);

      cache.formulas = formulasRes.status === "fulfilled" ? formulasRes.value : null;
      cache.glossary = glossaryRes.status === "fulfilled" ? glossaryRes.value : null;
      cache.dist = distRes.status === "fulfilled" ? distRes.value : null;
      renderActiveTab();
    }

    for (const btn of document.querySelectorAll("[data-open-reference]")) {
      btn.addEventListener("click", hydrate);
    }

    const observer = new window.MutationObserver(() => {
      if (refDialog.open) hydrate();
    });
    observer.observe(refDialog, { attributes: true, attributeFilter: ["open"] });

    ns.referenceDrawer = { hydrate };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initReferenceDrawer, { once: true });
  } else {
    initReferenceDrawer();
  }
})();
