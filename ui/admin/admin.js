(() => {
  "use strict";

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function announce(text) {
    const live = document.getElementById("admin-live");
    const out = document.getElementById("admin-status-output");
    if (out) out.textContent = text;
    if (live) {
      live.textContent = "";
      window.requestAnimationFrame(() => {
        live.textContent = text;
      });
    }
  }

  function recountStats() {
    const badges = document.querySelectorAll("[data-vis-badge]");
    let pub = 0;
    let hidden = 0;
    for (const badge of badges) {
      if (badge.classList.contains("vis-public")) {
        pub += 1;
      } else {
        hidden += 1;
      }
    }
    const pubEl = document.getElementById("stat-public-count");
    const hidEl = document.getElementById("stat-hidden-count");
    if (pubEl) pubEl.textContent = String(pub);
    if (hidEl) hidEl.textContent = String(hidden);
  }

  async function toggleVisibility(button) {
    const slug = button.dataset.slug;
    const current = button.dataset.currentVisibility || "public";
    const nextVis = current === "public" ? "admin" : "public";
    if (!slug) return;

    button.disabled = true;
    announce(`Aktualizuji viditelnost předmětu ${slug}…`);

    try {
      const headers = new Headers({
        Accept: "application/json",
        "Content-Type": "application/json",
      });
      const token = getCsrfToken();
      if (token) headers.set("X-CSRF-Token", token);

      const response = await fetch(`/api/admin/subjects/${encodeURIComponent(slug)}/visibility`, {
        method: "POST",
        credentials: "same-origin",
        headers,
        body: JSON.stringify({ visibility: nextVis }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      button.dataset.currentVisibility = nextVis;
      button.textContent = nextVis === "public" ? "Skrýt před veřejností" : "Zveřejnit";

      const badge = document.querySelector(`[data-vis-badge="${CSS.escape(slug)}"]`);
      if (badge) {
        badge.className = nextVis === "public" ? "vis-badge vis-public" : "vis-badge vis-admin";
        badge.textContent = nextVis === "public" ? "Veřejný" : "Skrytý (admin)";
      }

      recountStats();
      announce(
        nextVis === "public"
          ? `Předmět ${slug} je nyní veřejný`
          : `Předmět ${slug} je nyní skrytý`
      );
    } catch {
      announce(`Nepodařilo se změnit viditelnost předmětu ${slug}`);
    } finally {
      button.disabled = false;
    }
  }

  function initAdmin() {
    for (const btn of document.querySelectorAll("[data-toggle-visibility]")) {
      btn.addEventListener("click", () => toggleVisibility(btn));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAdmin, { once: true });
  } else {
    initAdmin();
  }
})();
