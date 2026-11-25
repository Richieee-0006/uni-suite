/**
 * Interactive DFA / NFA tester and <canvas> state diagram renderer (TZI /automat).
 * Uses ResizeObserver, devicePixelRatio, touch-action: pan-y, zero innerHTML / inline style=.
 */
(() => {
  "use strict";

  const ns = (window.UniSuite = window.UniSuite || {});

  function readCssColor(varName, fallback) {
    const val = window
      .getComputedStyle(document.documentElement)
      .getPropertyValue(varName)
      .trim();
    return val || fallback;
  }

  function simulateLocal(spec, word) {
    const trans = spec.transitions || {};
    const accept = new Set(spec.accept || []);
    const epsTokens = new Set(["", "ε", "eps"]);

    function epsClosure(states) {
      const seen = new Set(states);
      const stack = Array.from(states);
      while (stack.length > 0) {
        const cur = stack.pop();
        const edges = trans[cur] || {};
        for (const [sym, dst] of Object.entries(edges)) {
          if (!epsTokens.has(sym)) continue;
          const list = Array.isArray(dst) ? dst : [dst];
          for (const target of list) {
            if (target && !seen.has(target)) {
              seen.add(target);
              stack.push(target);
            }
          }
        }
      }
      return Array.from(seen).sort();
    }

    let current = epsClosure([spec.start]);
    const trace = [{ step: 0, symbol: "", states: current }];
    for (let i = 0; i < word.length; i += 1) {
      const ch = word[i];
      const next = new Set();
      for (const st of current) {
        const edges = trans[st] || {};
        const raw = edges[ch];
        if (!raw) continue;
        const targets = Array.isArray(raw) ? raw : [raw];
        for (const t of targets) {
          if (t) next.add(t);
        }
      }
      current = epsClosure(Array.from(next));
      trace.push({ step: i + 1, symbol: ch, states: current });
    }
    const accepted = current.some((s) => accept.has(s));
    return { accepted, final_states: current, trace };
  }

  function drawAutomaton(ctx, w, h, spec, activeStates) {
    const bg = readCssColor("--bg-input", "#141518");
    const surface = readCssColor("--bg-surface-2", "#1e2024");
    const border = readCssColor("--border-strong", "#64748b");
    const accent = readCssColor("--accent", "#38bdf8");
    const ok = readCssColor("--status-ok", "#34d399");
    const text = readCssColor("--text-1", "#eef0f3");
    const muted = readCssColor("--text-2", "#a6acb8");

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    const states = Array.isArray(spec.states) ? spec.states : [];
    if (states.length === 0) return;

    const acceptSet = new Set(spec.accept || []);
    const activeSet = new Set(activeStates || []);
    const cx = w / 2;
    const cy = h / 2;
    const rx = Math.max(60, Math.min(w * 0.34, 180));
    const ry = Math.max(46, Math.min(h * 0.3, 86));
    const radius = 22;

    const pos = new Map();
    states.forEach((st, idx) => {
      if (states.length === 1) {
        pos.set(st, { x: cx, y: cy });
      } else if (states.length <= 3) {
        const span = Math.min(w - 110, 280);
        const startX = cx - span / 2;
        const step = states.length > 1 ? span / (states.length - 1) : 0;
        pos.set(st, { x: startX + idx * step, y: cy });
      } else {
        const angle = -Math.PI + (2 * Math.PI * idx) / states.length;
        pos.set(st, { x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) });
      }
    });

    // Group edges by (src -> dst) to combine labels cleanly.
    const groupedEdges = new Map();
    const transitions = spec.transitions || {};
    for (const [src, map] of Object.entries(transitions)) {
      if (!map || typeof map !== "object") continue;
      for (const [sym, rawDst] of Object.entries(map)) {
        const dstList = Array.isArray(rawDst) ? rawDst : [rawDst];
        for (const dst of dstList) {
          if (!pos.has(src) || !pos.has(dst)) continue;
          const key = `${src}->${dst}`;
          const list = groupedEdges.get(key) || [];
          list.push(sym === "" ? "ε" : sym);
          groupedEdges.set(key, list);
        }
      }
    }

    function drawArrowHead(x, y, angle, color) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x - 8 * Math.cos(angle - 0.4), y - 8 * Math.sin(angle - 0.4));
      ctx.lineTo(x - 8 * Math.cos(angle + 0.4), y - 8 * Math.sin(angle + 0.4));
      ctx.closePath();
      ctx.fill();
    }

    // Draw transitions
    ctx.font = "12px monospace";
    ctx.textAlign = "center";
    for (const [key, labels] of groupedEdges.entries()) {
      const [src, dst] = key.split("->");
      const p1 = pos.get(src);
      const p2 = pos.get(dst);
      const label = labels.join(",");

      ctx.strokeStyle = muted;
      ctx.lineWidth = 1.5;

      if (src === dst) {
        ctx.beginPath();
        ctx.arc(p1.x, p1.y - radius - 10, 14, 0.25 * Math.PI, 0.75 * Math.PI, true);
        ctx.stroke();
        ctx.fillStyle = text;
        ctx.fillText(label, p1.x, p1.y - radius - 28);
        continue;
      }

      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const dist = Math.hypot(dx, dy) || 1;
      const nx = -dy / dist;
      const ny = dx / dist;
      const hasReverse = groupedEdges.has(`${dst}->${src}`);
      const curve = hasReverse ? 24 : 0;

      const midX = (p1.x + p2.x) / 2 + nx * curve;
      const midY = (p1.y + p2.y) / 2 + ny * curve;

      const angleEnd = Math.atan2(p2.y - midY, p2.x - midX);
      const endX = p2.x - (radius + 2) * Math.cos(angleEnd);
      const endY = p2.y - (radius + 2) * Math.sin(angleEnd);

      const angleStart = Math.atan2(midY - p1.y, midX - p1.x);
      const startX = p1.x + radius * Math.cos(angleStart);
      const startY = p1.y + radius * Math.sin(angleStart);

      ctx.beginPath();
      ctx.moveTo(startX, startY);
      ctx.quadraticCurveTo(midX, midY, endX, endY);
      ctx.stroke();
      drawArrowHead(endX, endY, angleEnd, muted);

      ctx.fillStyle = text;
      ctx.fillText(label, midX + nx * 10, midY + ny * 10 - 4);
    }

    // Start arrow into q0
    if (pos.has(spec.start)) {
      const sp = pos.get(spec.start);
      ctx.strokeStyle = accent;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(sp.x - radius - 26, sp.y);
      ctx.lineTo(sp.x - radius - 3, sp.y);
      ctx.stroke();
      drawArrowHead(sp.x - radius - 2, sp.y, 0, accent);
    }

    // Draw state circles
    for (const st of states) {
      const p = pos.get(st);
      const isActive = activeSet.has(st);
      const isAccept = acceptSet.has(st);

      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = isActive ? "rgba(56, 189, 248, 0.22)" : surface;
      ctx.fill();
      ctx.lineWidth = isActive ? 2.5 : 1.6;
      ctx.strokeStyle = isActive ? (isAccept ? ok : accent) : border;
      ctx.stroke();

      if (isAccept) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius - 5, 0, Math.PI * 2);
        ctx.lineWidth = 1.4;
        ctx.stroke();
      }

      ctx.fillStyle = text;
      ctx.font = "600 13px monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(st, p.x, p.y);
    }
  }

  async function mountAutomataWidget(container, slug = "tzi") {
    const dom = ns.dom;
    if (!dom || !container) return;

    let presets = [];
    try {
      const res = await dom.apiFetch(`/api/${encodeURIComponent(slug)}/tools/automata`);
      presets = Array.isArray(res && res.presets) ? res.presets : [];
    } catch {
      presets = [
        {
          id: "even_zeros",
          title: "Sudý počet nul (DKA)",
          spec: {
            states: ["q0", "q1"],
            alphabet: ["0", "1"],
            start: "q0",
            accept: ["q0"],
            transitions: {
              q0: { 0: "q1", 1: "q0" },
              q1: { 0: "q0", 1: "q1" },
            },
          },
          sample_words: ["1010", "110"],
        },
      ];
    }

    let currentPreset = presets[0];
    let activeStates = currentPreset ? [currentPreset.spec.start] : [];

    const canvas = dom.el("canvas", {
      className: "lab-canvas",
      role: "img",
      "aria-label": "Stavový diagram konečného automatu",
    });
    const wordInput = dom.el("input", {
      type: "text",
      className: "aut-word-input",
      placeholder: "Zadej vstupní slovo (např. 1010)",
      autocomplete: "off",
      autocapitalize: "off",
      spellcheck: "false",
    });
    const runBtn = dom.el(
      "button",
      { type: "button", className: "btn btn-primary btn-sm" },
      "Simulovat",
    );
    const verdictEl = dom.el("output", { className: "aut-verdict" }, "Připraveno");
    const traceEl = dom.el("div", { className: "aut-trace" });
    const presetStrip = dom.el("div", { className: "tab-strip" });

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    function redraw() {
      const rect = canvas.getBoundingClientRect();
      const w = Math.max(240, Math.round(rect.width || 600));
      const h = Math.max(180, Math.round(rect.height || 240));
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx || !currentPreset) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawAutomaton(ctx, w, h, currentPreset.spec, activeStates);
    }

    function runWord() {
      if (!currentPreset) return;
      const word = wordInput.value;
      const sim = simulateLocal(currentPreset.spec, word);
      activeStates = sim.final_states;
      verdictEl.textContent = sim.accepted
        ? `PŘIJATO (koncové stavy: ${sim.final_states.join(", ") || "∅"})`
        : `ZAMÍTNUTO (koncové stavy: ${sim.final_states.join(", ") || "∅"})`;
      verdictEl.dataset.accepted = sim.accepted ? "1" : "0";
      dom.clear(traceEl);
      for (const step of sim.trace) {
        const lbl = step.step === 0 ? "start" : `'${step.symbol}'`;
        traceEl.appendChild(
          dom.el(
            "span",
            { className: "aut-step-chip" },
            `${lbl} → {${step.states.join(", ")}}`,
          ),
        );
      }
      redraw();
    }

    presets.forEach((p, idx) => {
      const btn = dom.el(
        "button",
        {
          type: "button",
          className: "tab-pill",
          "aria-pressed": idx === 0 ? "true" : "false",
        },
        p.title,
      );
      btn.addEventListener("click", () => {
        currentPreset = p;
        activeStates = [p.spec.start];
        for (const other of presetStrip.querySelectorAll("button")) {
          other.setAttribute("aria-pressed", other === btn ? "true" : "false");
        }
        wordInput.value = (p.sample_words && p.sample_words[0]) || "";
        runWord();
      });
      presetStrip.appendChild(btn);
    });

    runBtn.addEventListener("click", runWord);
    wordInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        runWord();
      }
    });

    dom.clear(container);
    container.appendChild(
      dom.el(
        "section",
        { className: "lab-shell", "aria-label": "Simulátor konečných automatů" },
        dom.el(
          "div",
          { className: "lab-header" },
          dom.el("h3", {}, "Simulátor konečného automatu (DKA / NKA)"),
          presetStrip,
        ),
        dom.el("div", { className: "lab-canvas-wrap" }, canvas),
        dom.el("div", { className: "aut-word-row" }, wordInput, runBtn, verdictEl),
        traceEl,
      ),
    );

    if (typeof window.ResizeObserver === "function") {
      new window.ResizeObserver(() => redraw()).observe(canvas);
    }
    window.requestAnimationFrame(redraw);
  }

  function autoMountAutomata() {
    const host = document.getElementById("automata-host");
    if (!host) return;
    const state = ns.dom ? ns.dom.readState() : {};
    const slug = (state.subject && state.subject.slug) || "tzi";
    mountAutomataWidget(host, slug);
  }

  ns.automata = {
    mountAutomataWidget,
    simulateLocal,
    autoMountAutomata,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoMountAutomata, { once: true });
  } else {
    autoMountAutomata();
  }
})();
