(() => {
  "use strict";

  function initCmdPalette() {
    const dom = window.UniSuite && window.UniSuite.dom;
    const dialog = document.getElementById("cmd-palette-dialog");
    const form = document.getElementById("cmd-search-form");
    const input = document.getElementById("cmd-search-input");
    const resultsList = document.getElementById("cmd-results");

    if (!dom || !dialog || !input || !resultsList) return;

    let items = [];
    let activeIndex = -1;
    let debounceId = null;
    let abortCtrl = null;

    function getQuickActions() {
      return [
        {
          title: "Přehled předmětů",
          subtitle: "Hlavní stránka",
          url: "/",
        },
        {
          title: "Přizpůsobení rozhraní",
          subtitle: "Téma, akcent, hustota a moduly",
          action: () => {
            dialog.close();
            if (window.UniSuite.customizer) window.UniSuite.customizer.open();
          },
        },
        {
          title: "Přepnout Focus mód",
          subtitle: "Skrýt nebo zobrazit denní cíl a streak",
          action: () => {
            dialog.close();
            if (window.UniSuite.customizer) {
              const cur = window.UniSuite.customizer.get();
              window.UniSuite.customizer.updateKey("focus_mode", !cur.focus_mode);
            }
          },
        },
        {
          title: "Otevřít Tahák a vzorce",
          subtitle: "Klávesa F",
          action: () => {
            dialog.close();
            if (window.UniSuite.scratchpad) window.UniSuite.scratchpad.openReference();
          },
        },
        {
          title: "Otevřít Škrabák",
          subtitle: "Klávesa S",
          action: () => {
            dialog.close();
            if (window.UniSuite.scratchpad) window.UniSuite.scratchpad.openScratchpad();
          },
        },
      ];
    }

    function normalizeApiItem(raw) {
      if (!raw || typeof raw !== "object") return null;
      const title = raw.title || raw.label || raw.name || raw.code || "";
      if (!title) return null;
      const subtitle = raw.subtitle || raw.code || raw.category || raw.kind || "";
      const url = raw.url || raw.href || (raw.slug ? `/predmet/${raw.slug}` : "");
      return { title: String(title), subtitle: String(subtitle), url: String(url) };
    }

    function executeItem(item) {
      if (!item) return;
      if (typeof item.action === "function") {
        item.action();
        return;
      }
      if (item.url) {
        dialog.close();
        if (/^https?:\/\//i.test(item.url)) {
          window.open(item.url, "_blank", "noopener,noreferrer");
        } else if (item.url.startsWith("/")) {
          window.location.assign(item.url);
        }
      }
    }

    function updateActiveHighlight() {
      const buttons = resultsList.querySelectorAll(".cmd-item-btn");
      buttons.forEach((btn, idx) => {
        btn.dataset.active = idx === activeIndex ? "true" : "false";
        if (idx === activeIndex) {
          btn.scrollIntoView({ block: "nearest" });
        }
      });
    }

    function renderItems(list) {
      items = list;
      activeIndex = list.length > 0 ? 0 : -1;
      dom.clear(resultsList);

      if (list.length === 0) {
        resultsList.appendChild(
          dom.el("li", { className: "cmd-empty" }, "Žádné odpovídající výsledky")
        );
        return;
      }

      list.forEach((item, idx) => {
        const btn = dom.el(
          "button",
          {
            type: "button",
            className: "cmd-item-btn",
            dataset: { active: idx === 0 ? "true" : "false" },
            onClick: () => executeItem(item),
          },
          dom.el("span", null, item.title),
          item.subtitle ? dom.el("span", { className: "cmd-item-sub" }, item.subtitle) : null
        );
        resultsList.appendChild(dom.el("li", null, btn));
      });
    }

    async function runSearch(query) {
      const q = (query || "").trim();
      const quick = getQuickActions().filter((a) => {
        if (!q) return true;
        const hay = `${a.title} ${a.subtitle}`.toLowerCase();
        return hay.includes(q.toLowerCase());
      });

      if (abortCtrl) {
        abortCtrl.abort();
      }
      abortCtrl = new AbortController();

      try {
        const payload = await dom.apiFetch(`/api/search?q=${encodeURIComponent(q)}`, {
          signal: abortCtrl.signal,
        });
        const rawList = Array.isArray(payload)
          ? payload
          : Array.isArray(payload && payload.results)
            ? payload.results
            : Array.isArray(payload && payload.items)
              ? payload.items
              : [];
        const remote = rawList.map(normalizeApiItem).filter(Boolean);
        renderItems([...remote, ...quick]);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        renderItems(quick);
      }
    }

    function openPalette() {
      if (!dialog.open) {
        dialog.showModal();
      }
      input.value = "";
      runSearch("");
      window.requestAnimationFrame(() => input.focus());
    }

    for (const btn of document.querySelectorAll("[data-open-cmd]")) {
      btn.addEventListener("click", openPalette);
    }

    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (activeIndex >= 0 && items[activeIndex]) {
          executeItem(items[activeIndex]);
        } else {
          runSearch(input.value);
        }
      });
    }

    input.addEventListener("input", () => {
      window.clearTimeout(debounceId);
      debounceId = window.setTimeout(() => runSearch(input.value), 140);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (items.length > 0) {
          activeIndex = (activeIndex + 1) % items.length;
          updateActiveHighlight();
        }
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        if (items.length > 0) {
          activeIndex = (activeIndex - 1 + items.length) % items.length;
          updateActiveHighlight();
        }
      } else if (event.key === "Enter" && activeIndex >= 0 && items[activeIndex]) {
        event.preventDefault();
        executeItem(items[activeIndex]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        dialog.close();
      }
    });

    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (dialog.open) {
          dialog.close();
        } else {
          openPalette();
        }
        return;
      }

      if (
        event.key === "/" &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        document.documentElement.dataset.shortcuts !== "0" &&
        !dom.isEditableTarget(event.target) &&
        !document.querySelector("dialog[open]")
      ) {
        event.preventDefault();
        openPalette();
      }
    });

    window.UniSuite = window.UniSuite || {};
    window.UniSuite.cmdPalette = { open: openPalette };
  }

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.initCmdPalette = initCmdPalette;
})();
