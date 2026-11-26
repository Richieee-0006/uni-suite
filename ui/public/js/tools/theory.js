/**
 * Subject Theory & Glossary page enhancer (/predmet/{slug}/teorie).
 * Hydrates KaTeX MathML (strict Trusted Types 'none': ZERO innerHTML, ZERO inline style=),
 * live search filter across chapters/tables/examples/formulas/glossary, sticky outline
 * scrollspy (CSS scroll-target-group + IntersectionObserver fallback), and synced
 * Expand/Collapse All button for native <details> chapters.
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

  function initTheoryPage() {
    const dom = ns.dom;
    const katex = ns.katex;
    if (!dom) return;

    // 1. Hydrate inline & display KaTeX MathML without touching innerHTML.
    if (katex && typeof katex.renderRichText === "function") {
      for (const el of document.querySelectorAll("[data-rich-math]")) {
        const raw = el.textContent || "";
        if (raw.includes("$") || raw.includes("`") || raw.includes("**")) {
          katex.renderRichText(el, raw);
        }
      }
    }
    if (katex && typeof katex.renderMathNode === "function") {
      for (const el of document.querySelectorAll("[data-display-math]")) {
        const tex = (el.textContent || "").trim();
        if (!tex) continue;
        dom.clear(el);
        el.appendChild(katex.renderMathNode(tex, true));
      }
    }

    const accordion = document.getElementById("theory-chapters-accordion");
    const chapters = accordion
      ? Array.from(accordion.querySelectorAll(":scope > details.theory-chapter"))
      : [];
    const toggleAllBtn = document.querySelector("[data-theory-toggle-all]");

    function syncToggleAllState() {
      if (!toggleAllBtn || chapters.length === 0) return;
      const visibleChapters = chapters.filter((d) => !d.hidden);
      const allOpen =
        visibleChapters.length > 0 && visibleChapters.every((details) => details.open);
      toggleAllBtn.setAttribute("aria-expanded", allOpen ? "true" : "false");
    }

    if (toggleAllBtn && chapters.length > 0) {
      toggleAllBtn.addEventListener("click", () => {
        const shouldExpand = toggleAllBtn.getAttribute("aria-expanded") !== "true";
        for (const details of chapters) {
          if (!details.hidden) {
            details.open = shouldExpand;
          }
        }
        syncToggleAllState();
      });

      document.addEventListener(
        "toggle",
        (event) => {
          if (event.target instanceof HTMLDetailsElement && event.target.matches(".theory-chapter")) {
            syncToggleAllState();
          }
        },
        { capture: true },
      );
      syncToggleAllState();
    }

    // Open target chapter automatically when jumping from outline/TOC or URL fragment.
    function openTargetFromHash(hashId) {
      const cleanId = String(hashId || "").replace(/^#/, "");
      if (!cleanId) return;
      const target = document.getElementById(cleanId);
      if (target instanceof HTMLDetailsElement) {
        target.open = true;
        syncToggleAllState();
      }
    }

    for (const link of document.querySelectorAll("[data-theory-jump]")) {
      link.addEventListener("click", () => {
        openTargetFromHash(link.dataset.theoryJump);
      });
    }
    if (window.location.hash) {
      openTargetFromHash(window.location.hash);
    }
    window.addEventListener("hashchange", () => openTargetFromHash(window.location.hash));

    // 2. Scrollspy for sticky right-hand outline (native CSS scroll-target-group + IO fallback).
    const outlineLinks = Array.from(document.querySelectorAll(".theory-outline-nav a[href^='#']"));
    const spyTargets = Array.from(document.querySelectorAll("[data-spy-target][id]"));
    if (outlineLinks.length > 0 && spyTargets.length > 0) {
      const supportsNativeSpy =
        typeof CSS !== "undefined" &&
        typeof CSS.supports === "function" &&
        CSS.supports("scroll-target-group", "auto");

      if (supportsNativeSpy) {
        const syncNativeAria = () => {
          const active = document.querySelector(".theory-outline-nav a:target-current");
          for (const link of outlineLinks) {
            link.setAttribute("aria-current", link === active ? "true" : "false");
          }
        };
        syncNativeAria();
        document.addEventListener("scrollend", syncNativeAria);
      } else if ("IntersectionObserver" in window) {
        const setActiveId = (id) => {
          for (const link of outlineLinks) {
            const isActive = link.getAttribute("href") === `#${id}`;
            link.classList.toggle("target-current", isActive);
            link.setAttribute("aria-current", isActive ? "true" : "false");
          }
        };
        const observer = new window.IntersectionObserver(
          (entries) => {
            for (const entry of entries) {
              if (entry.isIntersecting && entry.target.id) {
                setActiveId(entry.target.id);
                break;
              }
            }
          },
          { rootMargin: "-20% 0px -65% 0px", threshold: 0 },
        );
        for (const section of spyTargets) {
          observer.observe(section);
        }
        setActiveId(spyTargets[0].id);
      }
    }

    // 3. Live search across chapters, tables, examples, formula cards, and glossary cards.
    const searchInput = document.querySelector("[data-theory-search]");
    const matchStatus = document.getElementById("theory-match-status");
    const searchableItems = Array.from(document.querySelectorAll("[data-theory-item]"));
    const searchableGroups = Array.from(document.querySelectorAll("[data-theory-group]"));
    const indexed = searchableItems.map((el) => ({
      el,
      isChapter: el instanceof HTMLDetailsElement && el.classList.contains("theory-chapter"),
      haystack: normalizeText(el.textContent || ""),
    }));
    const defaultStatus = matchStatus ? matchStatus.textContent : "";

    if (searchInput) {
      searchInput.addEventListener("input", () => {
        const q = normalizeText(searchInput.value.trim());
        let matchedCount = 0;

        for (const entry of indexed) {
          const hit = !q || entry.haystack.includes(q);
          entry.el.hidden = !hit;
          if (hit) {
            matchedCount += 1;
            if (q && entry.isChapter) {
              entry.el.open = true;
            }
          }
        }

        for (const group of searchableGroups) {
          const hasVisible = Boolean(group.querySelector("[data-theory-item]:not([hidden])"));
          group.hidden = !hasVisible;
        }

        if (matchStatus) {
          matchStatus.textContent = q ? `Nalezeno: ${matchedCount}` : defaultStatus;
        }
        syncToggleAllState();
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTheoryPage, { once: true });
  } else {
    initTheoryPage();
  }
})();
