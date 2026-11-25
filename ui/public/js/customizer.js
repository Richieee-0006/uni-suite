(() => {
  "use strict";

  const STORAGE_KEY = "uni_suite_prefs_v1";

  const DEFAULTS = {
    theme: "graphite",
    accent: "sky",
    density: "default",
    font_scale: 100,
    focus_mode: false,
    show_matrix: true,
    rail_state: "open",
    shortcuts: true,
    auto_advance: false,
    sound: false,
    code_layout: "split",
    reduced_motion: false,
    formula_hints: true,
    daily_goal: 5,
    block_size: 10,
    pinned_subjects: [],
  };

  const ALLOW = {
    theme: new Set(["graphite", "amoled", "light", "catppuccin", "auto"]),
    accent: new Set(["sky", "emerald", "amber", "rose", "violet"]),
    density: new Set(["compact", "default", "relaxed"]),
    font_scale: new Set([90, 100, 110, 120]),
    rail_state: new Set(["open", "collapsed"]),
    code_layout: new Set(["split", "stack"]),
    block_size: new Set([5, 10, 15, 20]),
  };

  function sanitizePrefs(raw) {
    const src = raw && typeof raw === "object" ? raw : {};
    const fontScale = Number(src.font_scale);
    const blockSize = Number(src.block_size);
    const dailyGoal = Number(src.daily_goal);

    return {
      theme: ALLOW.theme.has(src.theme) ? src.theme : DEFAULTS.theme,
      accent: ALLOW.accent.has(src.accent) ? src.accent : DEFAULTS.accent,
      density: ALLOW.density.has(src.density) ? src.density : DEFAULTS.density,
      font_scale: ALLOW.font_scale.has(fontScale) ? fontScale : DEFAULTS.font_scale,
      focus_mode: Boolean(src.focus_mode ?? DEFAULTS.focus_mode),
      show_matrix: Boolean(src.show_matrix ?? DEFAULTS.show_matrix),
      rail_state: ALLOW.rail_state.has(src.rail_state) ? src.rail_state : DEFAULTS.rail_state,
      shortcuts: Boolean(src.shortcuts ?? DEFAULTS.shortcuts),
      auto_advance: Boolean(src.auto_advance ?? DEFAULTS.auto_advance),
      sound: Boolean(src.sound ?? DEFAULTS.sound),
      code_layout: ALLOW.code_layout.has(src.code_layout) ? src.code_layout : DEFAULTS.code_layout,
      reduced_motion: Boolean(src.reduced_motion ?? DEFAULTS.reduced_motion),
      formula_hints: Boolean(src.formula_hints ?? DEFAULTS.formula_hints),
      daily_goal: Number.isFinite(dailyGoal)
        ? Math.min(30, Math.max(3, Math.round(dailyGoal)))
        : DEFAULTS.daily_goal,
      block_size: ALLOW.block_size.has(blockSize) ? blockSize : DEFAULTS.block_size,
      pinned_subjects: Array.isArray(src.pinned_subjects)
        ? src.pinned_subjects.filter((s) => typeof s === "string" && /^[a-z0-9_-]{1,32}$/.test(s)).slice(0, 20)
        : [],
    };
  }

  function applyToDocument(prefs) {
    const d = document.documentElement.dataset;
    d.theme = prefs.theme;
    d.accent = prefs.accent;
    d.density = prefs.density;
    d.fontScale = String(prefs.font_scale);
    d.focus = prefs.focus_mode ? "1" : "0";
    d.matrix = prefs.show_matrix ? "1" : "0";
    d.rail = prefs.rail_state;
    d.shortcuts = prefs.shortcuts ? "1" : "0";
    d.autoAdvance = prefs.auto_advance ? "1" : "0";
    d.sound = prefs.sound ? "1" : "0";
    d.codeLayout = prefs.code_layout;
    d.reducedMotion = prefs.reduced_motion ? "1" : "0";

    for (const btn of document.querySelectorAll("[data-toggle-focus]")) {
      btn.setAttribute("aria-pressed", prefs.focus_mode ? "true" : "false");
    }
  }

  function initCustomizer() {
    const dom = window.UniSuite && window.UniSuite.dom;
    const state = dom ? dom.readState() : {};
    const isAuthenticated = Boolean(state.user && state.user.is_authenticated);
    const dialog = document.getElementById("customizer-dialog");
    const statusOut = document.getElementById("customizer-status");
    const goalOut = document.getElementById("cust-daily-goal-out");

    let current = sanitizePrefs(state.prefs || DEFAULTS);

    if (!isAuthenticated) {
      try {
        const stored = window.localStorage.getItem(STORAGE_KEY);
        if (stored) {
          current = sanitizePrefs({ ...current, ...JSON.parse(stored) });
        }
      } catch {
        // Ignore storage restrictions.
      }
    }

    applyToDocument(current);

    let saveTimer = null;

    function syncFormControls() {
      if (!dialog) return;
      for (const radio of dialog.querySelectorAll('input[type="radio"]')) {
        const key = radio.name;
        if (key in current) {
          radio.checked = String(current[key]) === radio.value;
        }
      }
      for (const box of dialog.querySelectorAll('input[type="checkbox"][data-pref-key]')) {
        const key = box.dataset.prefKey;
        if (key && key in current) {
          box.checked = Boolean(current[key]);
        }
      }
      if (goalOut) {
        goalOut.textContent = `${current.daily_goal} otázek`;
      }
    }

    async function persist() {
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(current));
      } catch {
        // Ignore storage quota errors.
      }

      if (!isAuthenticated || !dom) {
        if (statusOut) statusOut.textContent = "Uloženo v prohlížeči";
        return;
      }

      if (statusOut) statusOut.textContent = "Ukládám…";
      try {
        await dom.apiFetch("/api/preferences", {
          method: "PUT",
          body: JSON.stringify(current),
        });
        if (statusOut) statusOut.textContent = "Synchronizováno";
      } catch {
        if (statusOut) statusOut.textContent = "Uloženo lokálně";
      }
    }

    function schedulePersist() {
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(persist, 180);
    }

    function updateKey(key, value) {
      current = sanitizePrefs({ ...current, [key]: value });
      applyToDocument(current);
      syncFormControls();
      schedulePersist();
    }

    function openDialog() {
      if (!dialog) return;
      syncFormControls();
      if (!dialog.open) {
        dialog.showModal();
      }
    }

    for (const btn of document.querySelectorAll("[data-open-customizer]")) {
      btn.addEventListener("click", openDialog);
    }

    if (dialog) {
      dialog.addEventListener("change", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement)) return;
        if (target.type === "radio" && target.name in DEFAULTS) {
          const val = target.name === "font_scale" || target.name === "block_size"
            ? Number(target.value)
            : target.value;
          updateKey(target.name, val);
        } else if (target.type === "checkbox" && target.dataset.prefKey) {
          updateKey(target.dataset.prefKey, target.checked);
        }
      });

      const decBtn = document.getElementById("cust-goal-dec");
      const incBtn = document.getElementById("cust-goal-inc");
      const resetBtn = document.getElementById("customizer-reset");

      if (decBtn) {
        decBtn.addEventListener("click", () => updateKey("daily_goal", current.daily_goal - 1));
      }
      if (incBtn) {
        incBtn.addEventListener("click", () => updateKey("daily_goal", current.daily_goal + 1));
      }
      if (resetBtn) {
        resetBtn.addEventListener("click", () => {
          current = sanitizePrefs(DEFAULTS);
          applyToDocument(current);
          syncFormControls();
          schedulePersist();
          if (dom) dom.announce("Výchozí nastavení rozhraní bylo obnoveno");
        });
      }
    }

    syncFormControls();

    window.UniSuite = window.UniSuite || {};
    window.UniSuite.customizer = {
      get: () => ({ ...current }),
      updateKey,
      open: openDialog,
    };
  }

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.initCustomizer = initCustomizer;
})();
