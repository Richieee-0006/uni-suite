(() => {
  "use strict";

  function initNavDrawer() {
    const rail = document.getElementById("nav-rail");
    const openBtn = document.getElementById("rail-open-btn");
    const closeBtn = document.getElementById("rail-close-btn");
    const scroller = document.getElementById("rail-scroller");
    const sentinel = document.getElementById("rail-backdrop-sentinel");
    const mainEl = document.getElementById("main-content");
    const topbarEl = document.getElementById("app-topbar");
    const footerEl = document.getElementById("app-footer");

    if (!rail) return;

    const mobileMq = window.matchMedia("(max-width: 63.99rem)");
    let isOpen = false;
    let lastTrigger = null;
    let observer = null;

    function setBackgroundInert(flag) {
      for (const node of [mainEl, topbarEl, footerEl]) {
        if (node) {
          node.inert = Boolean(flag);
        }
      }
    }

    function syncAriaMode() {
      if (mobileMq.matches) {
        if (!rail.hasAttribute("popover")) {
          rail.setAttribute("popover", "manual");
        }
        rail.setAttribute("role", "dialog");
        rail.setAttribute("aria-modal", isOpen ? "true" : "false");
        rail.setAttribute("aria-label", "Navigační panel");
      } else {
        if (isOpen && typeof rail.hidePopover === "function") {
          try {
            rail.hidePopover();
          } catch {
            // Popover may already be closed.
          }
        }
        isOpen = false;
        setBackgroundInert(false);
        rail.removeAttribute("role");
        rail.removeAttribute("aria-modal");
        rail.removeAttribute("aria-label");
        if (openBtn) {
          openBtn.setAttribute("aria-expanded", "false");
        }
      }
    }

    function openDrawer(trigger) {
      if (!mobileMq.matches) {
        const html = document.documentElement;
        const collapsed = html.dataset.rail === "collapsed";
        html.dataset.rail = collapsed ? "open" : "collapsed";
        if (openBtn) {
          openBtn.setAttribute("aria-expanded", collapsed ? "true" : "false");
        }
        if (window.UniSuite && window.UniSuite.customizer) {
          window.UniSuite.customizer.updateKey("rail_state", html.dataset.rail);
        }
        return;
      }

      lastTrigger = trigger || document.activeElement;
      if (!rail.hasAttribute("popover")) {
        rail.setAttribute("popover", "manual");
      }
      if (typeof rail.showPopover === "function") {
        try {
          rail.showPopover();
        } catch {
          rail.setAttribute("data-open-fallback", "1");
        }
      } else {
        rail.setAttribute("data-open-fallback", "1");
      }

      isOpen = true;
      rail.setAttribute("aria-modal", "true");
      setBackgroundInert(true);
      if (openBtn) {
        openBtn.setAttribute("aria-expanded", "true");
      }

      if (scroller) {
        scroller.scrollLeft = 0;
      }

      window.requestAnimationFrame(() => {
        if (closeBtn) {
          closeBtn.focus();
        }
      });
    }

    function closeDrawer(restoreFocus = true) {
      if (!isOpen) return;
      isOpen = false;
      if (typeof rail.hidePopover === "function") {
        try {
          rail.hidePopover();
        } catch {
          // Ignore if already closed.
        }
      }
      rail.removeAttribute("data-open-fallback");
      rail.setAttribute("aria-modal", "false");
      setBackgroundInert(false);

      if (openBtn) {
        openBtn.setAttribute("aria-expanded", "false");
      }
      if (restoreFocus && lastTrigger && typeof lastTrigger.focus === "function") {
        lastTrigger.focus();
      }
      lastTrigger = null;
    }

    if (openBtn) {
      openBtn.addEventListener("click", () => {
        if (mobileMq.matches && isOpen) {
          closeDrawer(true);
        } else {
          openDrawer(openBtn);
        }
      });
    }

    if (closeBtn) {
      closeBtn.addEventListener("click", () => closeDrawer(true));
    }

    if (sentinel) {
      sentinel.addEventListener("click", () => closeDrawer(true));
    }

    if (scroller && sentinel && "IntersectionObserver" in window) {
      observer = new IntersectionObserver(
        (entries) => {
          if (!isOpen || !mobileMq.matches) return;
          for (const entry of entries) {
            if (entry.isIntersecting && entry.intersectionRatio >= 0.85) {
              closeDrawer(true);
            }
          }
        },
        {
          root: scroller,
          threshold: [0.85],
        }
      );
      observer.observe(sentinel);
    }

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && isOpen && mobileMq.matches) {
        event.preventDefault();
        closeDrawer(true);
      }
    });

    rail.addEventListener("click", (event) => {
      if (!mobileMq.matches || !isOpen) return;
      const interactive = event.target instanceof Element ? event.target.closest("a, button[data-close-drawer]") : null;
      if (interactive) {
        closeDrawer(false);
      }
    });

    if (typeof mobileMq.addEventListener === "function") {
      mobileMq.addEventListener("change", syncAriaMode);
    }
    syncAriaMode();

    window.UniSuite = window.UniSuite || {};
    window.UniSuite.navDrawer = {
      open: openDrawer,
      close: closeDrawer,
      isOpen: () => isOpen,
    };
  }

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.initNavDrawer = initNavDrawer;
})();
