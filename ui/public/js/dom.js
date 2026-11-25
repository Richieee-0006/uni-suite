(() => {
  "use strict";

  function clear(node) {
    if (!node) return;
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs && typeof attrs === "object") {
      for (const [key, val] of Object.entries(attrs)) {
        if (val === null || val === undefined || val === false) continue;
        if (key === "style") continue;
        if (key === "className") {
          node.className = String(val);
        } else if (key === "dataset" && typeof val === "object") {
          for (const [dk, dv] of Object.entries(val)) {
            if (dv !== null && dv !== undefined) {
              node.dataset[dk] = String(dv);
            }
          }
        } else if (key.startsWith("on") && typeof val === "function") {
          node.addEventListener(key.slice(2).toLowerCase(), val);
        } else if (val === true) {
          node.setAttribute(key, "");
        } else {
          node.setAttribute(key, String(val));
        }
      }
    }

    for (const child of children) {
      if (child === null || child === undefined || child === false) continue;
      if (Array.isArray(child)) {
        for (const item of child) {
          if (item instanceof Node) {
            node.appendChild(item);
          } else if (item !== null && item !== undefined && item !== false) {
            node.appendChild(document.createTextNode(String(item)));
          }
        }
      } else if (child instanceof Node) {
        node.appendChild(child);
      } else {
        node.appendChild(document.createTextNode(String(child)));
      }
    }
    return node;
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function readState() {
    const script = document.getElementById("uni-state");
    if (!script || !script.textContent) return {};
    try {
      return JSON.parse(script.textContent);
    } catch {
      return {};
    }
  }

  function announce(message, urgent = false) {
    const target = document.getElementById(urgent ? "live-alert" : "live-polite");
    if (!target) return;
    target.textContent = "";
    window.requestAnimationFrame(() => {
      target.textContent = String(message || "");
    });
  }

  function isEditableTarget(target) {
    if (!target || !(target instanceof Element)) return false;
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    return target.isContentEditable === true;
  }

  async function apiFetch(url, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");

    if (method !== "GET" && method !== "HEAD") {
      const token = getCsrfToken();
      if (token) {
        headers.set("X-CSRF-Token", token);
      }
      if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }
    }

    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      method,
      headers,
    });

    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json().catch(() => null)
      : await response.text().catch(() => "");

    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" && payload.detail
          ? String(payload.detail)
          : `HTTP ${response.status}`;
      const error = new Error(detail);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }

    return payload;
  }

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.dom = {
    el,
    clear,
    getCsrfToken,
    readState,
    announce,
    isEditableTarget,
    apiFetch,
  };
})();
