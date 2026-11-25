/**
 * Responsive <canvas> interactive Mini-Lab (PAS distributions & NUM root-finding).
 * Uses ResizeObserver, devicePixelRatio, touch-action: pan-y, 44px sliders + <output>.
 * Zero external libraries, zero innerHTML, zero inline style=.
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

  function setupResponsiveCanvas(canvas, drawFn) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    function resizeAndDraw() {
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(240, Math.round(rect.width || 600));
      const height = Math.max(180, Math.round(rect.height || 256));
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawFn(ctx, width, height);
    }

    if (typeof window.ResizeObserver === "function") {
      const ro = new window.ResizeObserver(() => resizeAndDraw());
      ro.observe(canvas);
    }
    window.requestAnimationFrame(resizeAndDraw);
    return resizeAndDraw;
  }

  // Pure JS math helpers for smooth 60fps PAS slider dragging.
  function erfApprox(x) {
    const sign = x < 0 ? -1 : 1;
    const ax = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * ax);
    const poly =
      (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t +
        0.254829592) *
        t);
    return sign * (1 - poly * Math.exp(-ax * ax));
  }

  function normalPdf(x, mu, sigma) {
    const z = (x - mu) / sigma;
    return Math.exp(-0.5 * z * z) / (sigma * Math.sqrt(2 * Math.PI));
  }

  function normalCdf(x, mu, sigma) {
    return 0.5 * (1 + erfApprox((x - mu) / (sigma * Math.SQRT2)));
  }

  function lgamma(z) {
    const p = [
      676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059,
      12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
    ];
    if (z < 0.5) {
      return Math.log(Math.PI / Math.sin(Math.PI * z)) - lgamma(1 - z);
    }
    const zm = z - 1;
    let x = 0.99999999999980993;
    for (let i = 0; i < p.length; i += 1) {
      x += p[i] / (zm + i + 1);
    }
    const t = zm + p.length - 0.5;
    return 0.5 * Math.log(2 * Math.PI) + (zm + 0.5) * Math.log(t) - t + Math.log(x);
  }

  function tPdf(x, df) {
    const coeff = Math.exp(lgamma((df + 1) / 2) - lgamma(df / 2)) / Math.sqrt(df * Math.PI);
    return coeff * Math.pow(1 + (x * x) / df, -(df + 1) / 2);
  }

  function makeSlider(dom, { id, label, min, max, step, value, format, onInput }) {
    const out = dom.el("output", { className: "lab-output", for: id }, format(value));
    const input = dom.el("input", {
      type: "range",
      id,
      className: "lab-range",
      min: String(min),
      max: String(max),
      step: String(step),
      value: String(value),
    });
    input.addEventListener("input", () => {
      const v = Number(input.value);
      out.textContent = format(v);
      onInput(v);
    });
    return {
      element: dom.el(
        "div",
        { className: "lab-slider-field" },
        dom.el(
          "div",
          { className: "lab-slider-head" },
          dom.el("label", { for: id }, label),
          out,
        ),
        input,
      ),
      input,
      out,
    };
  }

  function mountPasDistLab(container) {
    const dom = ns.dom;
    if (!dom || !container) return;

    const params = { dist: "normal", mu: 0, sigma: 1, n: 25, df: 10, xObs: 1.96 };
    const canvas = dom.el("canvas", {
      className: "lab-canvas",
      role: "img",
      "aria-label": "Graf hustoty pravděpodobnostního rozdělení a kritického oboru",
    });
    const readout = dom.el("div", { className: "lab-readout-strip" });

    const normBtn = dom.el(
      "button",
      { type: "button", className: "tab-pill", "aria-pressed": "true" },
      "Normální N(μ, σ²)",
    );
    const tBtn = dom.el(
      "button",
      { type: "button", className: "tab-pill", "aria-pressed": "false" },
      "Studentovo t(df)",
    );

    function draw(ctx, w, h) {
      const bg = readCssColor("--bg-input", "#141518");
      const axis = readCssColor("--border-strong", "#3f4450");
      const accent = readCssColor("--accent", "#38bdf8");
      const err = readCssColor("--status-err", "#f87171");
      const text = readCssColor("--text-2", "#a6acb8");

      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);

      const padL = 36;
      const padR = 16;
      const padT = 16;
      const padB = 28;
      const plotW = w - padL - padR;
      const plotH = h - padT - padB;

      const isNorm = params.dist === "normal";
      const se = isNorm ? params.sigma / Math.sqrt(Math.max(1, params.n)) : 1;
      const xMin = isNorm ? params.mu - 4 * params.sigma : -5;
      const xMax = isNorm ? params.mu + 4 * params.sigma : 5;
      const pdfFn = (x) =>
        isNorm ? normalPdf(x, params.mu, params.sigma) : tPdf(x, params.df);

      let yMax = 0.01;
      const pts = [];
      for (let i = 0; i <= 160; i += 1) {
        const x = xMin + ((xMax - xMin) * i) / 160;
        const y = pdfFn(x);
        if (y > yMax) yMax = y;
        pts.push([x, y]);
      }
      yMax *= 1.12;

      const toX = (x) => padL + ((x - xMin) / (xMax - xMin)) * plotW;
      const toY = (y) => padT + plotH - (y / yMax) * plotH;

      // Shaded right-tail critical region for x >= xObs
      ctx.beginPath();
      let started = false;
      for (const [x, y] of pts) {
        if (x >= params.xObs) {
          if (!started) {
            ctx.moveTo(toX(x), toY(0));
            started = true;
          }
          ctx.lineTo(toX(x), toY(y));
        }
      }
      if (started) {
        ctx.lineTo(toX(xMax), toY(0));
        ctx.closePath();
        ctx.fillStyle = "rgba(248, 113, 113, 0.28)";
        ctx.fill();
      }

      // Baseline axis
      ctx.strokeStyle = axis;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, toY(0));
      ctx.lineTo(padL + plotW, toY(0));
      ctx.stroke();

      // PDF curve
      ctx.strokeStyle = accent;
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      pts.forEach(([x, y], idx) => {
        if (idx === 0) ctx.moveTo(toX(x), toY(y));
        else ctx.lineTo(toX(x), toY(y));
      });
      ctx.stroke();

      // Vertical marker at xObs
      const cx = toX(Math.max(xMin, Math.min(xMax, params.xObs)));
      ctx.strokeStyle = err;
      ctx.lineWidth = 1.6;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(cx, padT);
      ctx.lineTo(cx, toY(0));
      ctx.stroke();
      ctx.setLineDash([]);

      // Axis ticks
      ctx.fillStyle = text;
      ctx.font = "11px monospace";
      ctx.textAlign = "center";
      for (let i = 0; i <= 4; i += 1) {
        const xv = xMin + ((xMax - xMin) * i) / 4;
        ctx.fillText(xv.toFixed(1), toX(xv), h - 8);
      }

      // Update live numerical readout
      const cdf = isNorm
        ? normalCdf(params.xObs, params.mu, params.sigma)
        : 0.5 + 0.5 * erfApprox(params.xObs / Math.SQRT2);
      const rightTail = Math.max(0, 1 - cdf);
      const twoTail = Math.min(1, 2 * Math.min(cdf, rightTail));
      dom.clear(readout);
      readout.appendChild(
        dom.el("span", {}, `F(x) = ${cdf.toFixed(4)}`),
      );
      readout.appendChild(
        dom.el("span", {}, `p (pravostr.) = ${rightTail.toFixed(4)}`),
      );
      readout.appendChild(
        dom.el("span", {}, `p (oboustr.) = ${twoTail.toFixed(4)}`),
      );
      if (isNorm) {
        readout.appendChild(
          dom.el("span", {}, `SE = σ/√n = ${se.toFixed(3)}`),
        );
      }
    }

    const redraw = setupResponsiveCanvas(canvas, draw);

    normBtn.addEventListener("click", () => {
      params.dist = "normal";
      normBtn.setAttribute("aria-pressed", "true");
      tBtn.setAttribute("aria-pressed", "false");
      redraw();
    });
    tBtn.addEventListener("click", () => {
      params.dist = "t";
      tBtn.setAttribute("aria-pressed", "true");
      normBtn.setAttribute("aria-pressed", "false");
      redraw();
    });

    const controls = dom.el(
      "div",
      { className: "lab-controls" },
      makeSlider(dom, {
        id: "pas-lab-mu",
        label: "Střední hodnota μ",
        min: -10,
        max: 10,
        step: 0.5,
        value: params.mu,
        format: (v) => v.toFixed(1),
        onInput: (v) => {
          params.mu = v;
          redraw();
        },
      }).element,
      makeSlider(dom, {
        id: "pas-lab-sigma",
        label: "Směr. odchylka σ",
        min: 0.5,
        max: 5,
        step: 0.1,
        value: params.sigma,
        format: (v) => v.toFixed(1),
        onInput: (v) => {
          params.sigma = v;
          redraw();
        },
      }).element,
      makeSlider(dom, {
        id: "pas-lab-n",
        label: "Rozsah výběru n",
        min: 4,
        max: 100,
        step: 1,
        value: params.n,
        format: (v) => String(Math.round(v)),
        onInput: (v) => {
          params.n = v;
          redraw();
        },
      }).element,
      makeSlider(dom, {
        id: "pas-lab-df",
        label: "Stupně volnosti df",
        min: 1,
        max: 60,
        step: 1,
        value: params.df,
        format: (v) => String(Math.round(v)),
        onInput: (v) => {
          params.df = v;
          redraw();
        },
      }).element,
      makeSlider(dom, {
        id: "pas-lab-x",
        label: "Kritická mez x",
        min: -5,
        max: 5,
        step: 0.05,
        value: params.xObs,
        format: (v) => v.toFixed(2),
        onInput: (v) => {
          params.xObs = v;
          redraw();
        },
      }).element,
    );

    dom.clear(container);
    container.appendChild(
      dom.el(
        "section",
        { className: "lab-shell", "aria-label": "Interaktivní mini-lab rozdělení PAS" },
        dom.el(
          "div",
          { className: "lab-header" },
          dom.el("h3", {}, "Mini-lab · Rozdělení a p-hodnota"),
          dom.el("div", { className: "tab-strip" }, normBtn, tBtn),
        ),
        dom.el("div", { className: "lab-canvas-wrap" }, canvas),
        readout,
        controls,
      ),
    );
  }

  function mountNumRootLab(container) {
    const dom = ns.dom;
    if (!dom || !container) return;

    const params = { method: "bisection", c: 2.0, x0: 2.5, steps: 5 };
    const canvas = dom.el("canvas", {
      className: "lab-canvas",
      role: "img",
      "aria-label": "Graf konvergence bisekce a Newtonovy metody",
    });
    const readout = dom.el("div", { className: "lab-readout-strip" });
    const tableHost = dom.el("div", { className: "lab-table-wrap" });

    const bisBtn = dom.el(
      "button",
      { type: "button", className: "tab-pill", "aria-pressed": "true" },
      "Bisekce",
    );
    const newtBtn = dom.el(
      "button",
      { type: "button", className: "tab-pill", "aria-pressed": "false" },
      "Newton–Raphson",
    );

    function f(x) {
      return x * x - params.c;
    }

    function df(x) {
      return 2 * x;
    }

    function computeSteps() {
      const rows = [];
      if (params.method === "bisection") {
        let a = 0;
        let b = Math.max(2, Math.ceil(Math.sqrt(params.c)) + 1);
        for (let k = 1; k <= params.steps; k += 1) {
          const mid = (a + b) / 2;
          const fm = f(mid);
          rows.push({ k, x: mid, fx: fm, extra: (b - a) / 2 });
          if (f(a) * fm <= 0) b = mid;
          else a = mid;
        }
      } else {
        let x = Math.max(0.25, params.x0);
        for (let k = 1; k <= params.steps; k += 1) {
          const fx = f(x);
          const next = x - fx / df(x);
          rows.push({ k, x, fx, next, extra: Math.abs(next - x) });
          x = next;
        }
      }
      return rows;
    }

    function draw(ctx, w, h) {
      const bg = readCssColor("--bg-input", "#141518");
      const axis = readCssColor("--border-strong", "#3f4450");
      const accent = readCssColor("--accent", "#38bdf8");
      const ok = readCssColor("--status-ok", "#34d399");
      const warn = readCssColor("--status-warn", "#fbbf24");
      const text = readCssColor("--text-2", "#a6acb8");

      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);

      const padL = 36;
      const padR = 16;
      const padT = 16;
      const padB = 28;
      const plotW = w - padL - padR;
      const plotH = h - padT - padB;

      const xMin = 0;
      const xMax = Math.max(3.2, Math.sqrt(params.c) + 1.5, params.x0 + 0.5);
      const yMin = -params.c - 1;
      const yMax = f(xMax) + 1;

      const toX = (x) => padL + ((x - xMin) / (xMax - xMin)) * plotW;
      const toY = (y) => padT + plotH - ((y - yMin) / (yMax - yMin)) * plotH;

      // y = 0 axis
      ctx.strokeStyle = axis;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, toY(0));
      ctx.lineTo(padL + plotW, toY(0));
      ctx.stroke();

      // Curve f(x) = x^2 - c
      ctx.strokeStyle = accent;
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      for (let i = 0; i <= 100; i += 1) {
        const xv = xMin + ((xMax - xMin) * i) / 100;
        const yv = f(xv);
        if (i === 0) ctx.moveTo(toX(xv), toY(yv));
        else ctx.lineTo(toX(xv), toY(yv));
      }
      ctx.stroke();

      const rows = computeSteps();
      rows.forEach((row) => {
        if (params.method === "newton" && Number.isFinite(row.next)) {
          ctx.strokeStyle = warn;
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.moveTo(toX(row.x), toY(row.fx));
          ctx.lineTo(toX(row.next), toY(0));
          ctx.stroke();
        }
        ctx.fillStyle = ok;
        ctx.beginPath();
        ctx.arc(toX(row.x), toY(row.fx), 4, 0, Math.PI * 2);
        ctx.fill();
      });

      ctx.fillStyle = text;
      ctx.font = "11px monospace";
      ctx.textAlign = "center";
      for (let i = 0; i <= 4; i += 1) {
        const xv = xMin + ((xMax - xMin) * i) / 4;
        ctx.fillText(xv.toFixed(1), toX(xv), h - 8);
      }

      const last = rows[rows.length - 1];
      const approx = last ? (params.method === "newton" ? last.next : last.x) : 0;
      const exact = Math.sqrt(params.c);
      dom.clear(readout);
      readout.appendChild(dom.el("span", {}, `f(x) = x² − ${params.c.toFixed(1)}`));
      readout.appendChild(dom.el("span", {}, `Aproximace: ${approx.toFixed(6)}`));
      readout.appendChild(dom.el("span", {}, `√c = ${exact.toFixed(6)}`));
      readout.appendChild(dom.el("span", {}, `Chyba: ${Math.abs(approx - exact).toExponential(2)}`));

      dom.clear(tableHost);
      const tbody = dom.el("tbody");
      for (const r of rows) {
        tbody.appendChild(
          dom.el(
            "tr",
            {},
            dom.el("td", {}, String(r.k)),
            dom.el("td", {}, r.x.toFixed(5)),
            dom.el("td", {}, r.fx.toFixed(5)),
            dom.el("td", {}, r.extra.toExponential(2)),
          ),
        );
      }
      tableHost.appendChild(
        dom.el(
          "table",
          { className: "lab-table" },
          dom.el(
            "thead",
            {},
            dom.el(
              "tr",
              {},
              dom.el("th", { scope: "col" }, "k"),
              dom.el("th", { scope: "col" }, "x_k"),
              dom.el("th", { scope: "col" }, "f(x_k)"),
              dom.el("th", { scope: "col" }, "odhad chyby"),
            ),
          ),
          tbody,
        ),
      );
    }

    const redraw = setupResponsiveCanvas(canvas, draw);

    bisBtn.addEventListener("click", () => {
      params.method = "bisection";
      bisBtn.setAttribute("aria-pressed", "true");
      newtBtn.setAttribute("aria-pressed", "false");
      redraw();
    });
    newtBtn.addEventListener("click", () => {
      params.method = "newton";
      newtBtn.setAttribute("aria-pressed", "true");
      bisBtn.setAttribute("aria-pressed", "false");
      redraw();
    });

    const controls = dom.el(
      "div",
      { className: "lab-controls" },
      makeSlider(dom, {
        id: "num-lab-c",
        label: "Parametr c (kořen √c)",
        min: 1.5,
        max: 12,
        step: 0.5,
        value: params.c,
        format: (v) => v.toFixed(1),
        onInput: (v) => {
          params.c = v;
          redraw();
        },
      }).element,
      makeSlider(dom, {
        id: "num-lab-x0",
        label: "Počáteční odhad x₀",
        min: 0.5,
        max: 4.5,
        step: 0.1,
        value: params.x0,
        format: (v) => v.toFixed(1),
        onInput: (v) => {
          params.x0 = v;
          redraw();
        },
      }).element,
      makeSlider(dom, {
        id: "num-lab-steps",
        label: "Počet iterací k",
        min: 1,
        max: 10,
        step: 1,
        value: params.steps,
        format: (v) => String(Math.round(v)),
        onInput: (v) => {
          params.steps = Math.round(v);
          redraw();
        },
      }).element,
    );

    dom.clear(container);
    container.appendChild(
      dom.el(
        "section",
        { className: "lab-shell", "aria-label": "Interaktivní mini-lab hledání kořenů NUM" },
        dom.el(
          "div",
          { className: "lab-header" },
          dom.el("h3", {}, "Mini-lab · Konvergence bisekce a Newtona"),
          dom.el("div", { className: "tab-strip" }, bisBtn, newtBtn),
        ),
        dom.el("div", { className: "lab-canvas-wrap" }, canvas),
        readout,
        controls,
        tableHost,
      ),
    );
  }

  function autoMountMiniLab() {
    const dom = ns.dom;
    if (!dom) return;
    const state = dom.readState();
    const host = document.getElementById("mini-lab-host");
    if (!host) return;
    const kind = host.dataset.labKind || (state.subject && state.subject.slug === "pas" ? "pas" : "num");
    if (kind === "pas" || kind === "dist_tables") {
      mountPasDistLab(host);
    } else {
      mountNumRootLab(host);
    }
  }

  ns.miniLab = {
    mountPasDistLab,
    mountNumRootLab,
    autoMountMiniLab,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoMountMiniLab, { once: true });
  } else {
    autoMountMiniLab();
  }
})();
