/**
 * Event-driven UNI Engine v2 Host.
 * Wires subject page CTA, mode picker, topic picker, filter pills, active quiz view,
 * binary progress matrix (1/0/·), exam timer, sticky thumb bar, and live-region updates.
 * Strict CSP / Trusted Types 'none' compliant: ZERO innerHTML, ZERO inline style=.
 */
(() => {
  "use strict";

  const ns = (window.UniSuite = window.UniSuite || {});

  function createEngineHost() {
    const dom = ns.dom;
    const katex = ns.katex;
    const modesApi = ns.modes;
    const hintsApi = ns.hints;
    if (!dom) return null;

    const state = dom.readState();
    const subject = state && state.subject ? state.subject : null;
    const workspace = document.getElementById("quiz-workspace");
    const subjectGrid = document.querySelector(".subject-grid");
    const matrixWrap = document.querySelector(".binary-matrix");

    const session = {
      active: false,
      slug: subject ? subject.slug : "",
      mode: "blocks",
      topic: "",
      typeFilter: "all",
      questions: [],
      index: 0,
      results: [],
      verifiedCurrent: false,
      currentRenderer: null,
      currentHints: null,
      startedAtMs: 0,
      timerId: null,
      remainingSec: 0,
      studyPhasePending: false,
    };

    function getSelectedTopic() {
      const pressed = document.querySelector("[data-topic-select][aria-pressed='true']");
      return pressed ? String(pressed.dataset.topicSelect || "") : "";
    }

    function getSelectedTypeFilter() {
      const pressed = document.querySelector("[data-filter-type][aria-pressed='true']");
      return pressed ? String(pressed.dataset.filterType || "all") : "all";
    }

    function getBlockSize() {
      const prefs = (ns.customizer && ns.customizer.get()) || state.prefs || {};
      const parsed = Number.parseInt(String(prefs.block_size || 10), 10);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : 10;
    }

    function stopTimer() {
      if (session.timerId !== null) {
        window.clearInterval(session.timerId);
        session.timerId = null;
      }
    }

    function formatClock(totalSec) {
      const clamped = Math.max(0, Math.floor(totalSec));
      const m = Math.floor(clamped / 60);
      const s = clamped % 60;
      return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }

    function updateBinaryMatrix(correct) {
      if (!matrixWrap) return;
      const cells = Array.from(matrixWrap.querySelectorAll(".bin-cell"));
      const target =
        cells.find((c) => c.textContent === "·") ||
        cells[session.results.length - 1] ||
        null;
      if (!target) {
        matrixWrap.appendChild(
          dom.el(
            "span",
            { className: `bin-cell ${correct ? "bin-cell-1" : "bin-cell-0"}` },
            correct ? "1" : "0",
          ),
        );
        return;
      }
      target.classList.remove("bin-cell-1", "bin-cell-0");
      target.classList.add(correct ? "bin-cell-1" : "bin-cell-0");
      target.textContent = correct ? "1" : "0";
    }

    function closeWorkspace() {
      stopTimer();
      session.active = false;
      session.currentRenderer = null;
      session.currentHints = null;
      if (workspace) {
        dom.clear(workspace);
        workspace.hidden = true;
      }
      if (subjectGrid) {
        subjectGrid.hidden = false;
      }
      dom.announce("Návrat na přehled předmětu");
    }

    async function finishSession() {
      stopTimer();
      const total = session.questions.length;
      const correctCount = session.results.filter((r) => r.correct).length;
      const durationSec = Math.max(1, Math.round((Date.now() - session.startedAtMs) / 1000));
      const modeCfg = modesApi ? modesApi.getMode(session.mode) : { label: session.mode };
      let savedNote = "";

      if (modeCfg && modeCfg.saveScoreOnFinish && session.slug) {
        try {
          const resp = await dom.apiFetch(
            `/api/${encodeURIComponent(session.slug)}/exam/finish`,
            {
              method: "POST",
              body: JSON.stringify({
                mode: session.mode,
                score: correctCount,
                total,
                duration_sec: durationSec,
              }),
            },
          );
          if (resp && resp.saved) {
            savedNote = resp.passed
              ? `Uloženo (${resp.pct} % · splněno)`
              : `Uloženo (${resp.pct} % · nesplněno)`;
          }
        } catch {
          savedNote = "Výsledek nebylo možné uložit na server.";
        }
      }

      if (!workspace) return;
      dom.clear(workspace);

      const pct = total > 0 ? Math.round((100 * correctCount) / total) : 0;
      const backBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-secondary" },
        "Zpět na přehled",
      );
      backBtn.addEventListener("click", closeWorkspace);

      const retryBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-primary" },
        "Spustit znovu",
      );
      retryBtn.addEventListener("click", () => {
        startMode(session.mode, { topic: session.topic, type: session.typeFilter });
      });

      const matrixRow = dom.el("div", { className: "binary-matrix binary-matrix-wrap" });
      for (const r of session.results) {
        matrixRow.appendChild(
          dom.el(
            "span",
            { className: `bin-cell ${r.correct ? "bin-cell-1" : "bin-cell-0"}` },
            r.correct ? "1" : "0",
          ),
        );
      }

      workspace.appendChild(
        dom.el(
          "div",
          { className: "eng-summary" },
          dom.el(
            "div",
            { className: "eng-header" },
            dom.el("h2", {}, `Dokončeno · ${modeCfg ? modeCfg.label : session.mode}`),
            savedNote ? dom.el("span", { className: "eng-mode-pill" }, savedNote) : null,
          ),
          dom.el(
            "div",
            { className: "eng-summary-stats" },
            dom.el(
              "div",
              { className: "eng-stat-box" },
              dom.el("span", { className: "eng-stat-value" }, `${correctCount}/${total}`),
              dom.el("span", { className: "eng-stat-label" }, "Správně"),
            ),
            dom.el(
              "div",
              { className: "eng-stat-box" },
              dom.el("span", { className: "eng-stat-value" }, `${pct} %`),
              dom.el("span", { className: "eng-stat-label" }, "Úspěšnost"),
            ),
            dom.el(
              "div",
              { className: "eng-stat-box" },
              dom.el("span", { className: "eng-stat-value" }, formatClock(durationSec)),
              dom.el("span", { className: "eng-stat-label" }, "Čas"),
            ),
          ),
          matrixRow,
          dom.el("div", { className: "eng-action-primary" }, retryBtn, backBtn),
        ),
      );
      dom.announce(`Relace dokončena: ${correctCount} z ${total} správně (${pct} procent)`);
    }

    async function renderStudyBriefBeforeTest() {
      if (!workspace) return;
      dom.clear(workspace);
      const startTestBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-primary" },
        "Přejít k testu →",
      );
      const cancelBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-ghost" },
        "Zpět",
      );
      cancelBtn.addEventListener("click", closeWorkspace);
      startTestBtn.addEventListener("click", () => {
        session.studyPhasePending = false;
        renderCurrentQuestion();
      });

      const formulaBox = dom.el("div", { className: "ref-list" });
      if (session.slug) {
        try {
          const data = await dom.apiFetch(
            `/api/${encodeURIComponent(session.slug)}/tools/formulas`,
          );
          const items = (data && Array.isArray(data.items) ? data.items : []).slice(0, 4);
          for (const item of items) {
            const exprEl = dom.el("div", { className: "ref-expr" });
            if (katex && item.latex) {
              exprEl.appendChild(katex.renderMathNode(item.latex, true));
            } else {
              exprEl.textContent = item.latex || "";
            }
            formulaBox.appendChild(
              dom.el(
                "article",
                { className: "ref-card" },
                dom.el("h3", { className: "ref-name" }, item.name || "Vzorec"),
                exprEl,
                item.note ? dom.el("p", { className: "ref-note" }, item.note) : null,
              ),
            );
          }
        } catch {
          // Optional study overview.
        }
      }

      workspace.appendChild(
        dom.el(
          "div",
          { className: "eng-workspace" },
          dom.el("h2", {}, "Studuj, pak testuj — rychlý přehled"),
          dom.el(
            "p",
            { className: "page-subtitle" },
            `Prohlédni si klíčové vzorce a poté projdi ${session.questions.length} připravených otázek.`,
          ),
          formulaBox,
          dom.el("div", { className: "eng-action-primary" }, startTestBtn, cancelBtn),
        ),
      );
    }

    function renderCurrentQuestion() {
      if (!workspace) return;
      if (session.index >= session.questions.length) {
        finishSession();
        return;
      }

      const question = session.questions[session.index];
      const qSlug = question.subject_slug || session.slug;
      const modeCfg = modesApi ? modesApi.getMode(session.mode) : { label: session.mode };
      const qtype = String(question.type || "choice").toLowerCase();
      const rendererFactory =
        (ns.renderers && (ns.renderers.get(qtype) || ns.renderers.get("input_exact"))) ||
        null;

      session.verifiedCurrent = false;
      dom.clear(workspace);

      const timerEl = dom.el(
        "span",
        {
          className: "eng-timer",
          id: "eng-exam-timer",
          hidden: !(modeCfg && modeCfg.timed),
        },
        modeCfg && modeCfg.timed ? formatClock(session.remainingSec) : "",
      );

      const exitBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-ghost btn-sm" },
        "Ukončit",
      );
      exitBtn.addEventListener("click", closeWorkspace);

      const headerMeta = dom.el(
        "div",
        { className: "eng-header-meta" },
        dom.el("span", { className: "eng-mode-pill" }, modeCfg ? modeCfg.label : session.mode),
        question.subject_code
          ? dom.el("span", { className: "code-pill" }, String(question.subject_code))
          : null,
        question.leech
          ? dom.el("span", { className: "eng-leech-pill" }, "Problémová (leech)")
          : null,
        dom.el(
          "span",
          { className: "eng-counter" },
          `${session.index + 1} / ${session.questions.length}`,
        ),
      );

      const progressEl = dom.el("progress", {
        className: "eng-progress",
        value: String(session.index),
        max: String(Math.max(1, session.questions.length)),
        "aria-label": "Postup v sadě otázek",
      });

      const promptEl = dom.el("div", { className: "eng-question-text" });
      if (katex && typeof katex.renderRichText === "function") {
        katex.renderRichText(promptEl, String(question.text || ""));
      } else {
        promptEl.textContent = String(question.text || "");
      }

      const feedbackSlot = dom.el("div", { className: "eng-feedback", hidden: true });
      const verifyBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-primary", disabled: true },
        "Ověřit odpověď",
      );
      const nextBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-primary", hidden: true },
        session.index + 1 < session.questions.length ? "Další otázka →" : "Dokončit →",
      );

      const renderer = rendererFactory
        ? rendererFactory(question, {
            onAnswerChange: () => {
              if (!session.verifiedCurrent) {
                verifyBtn.disabled = !renderer.hasAnswer();
              }
            },
            onSubmit: () => {
              if (!session.verifiedCurrent && renderer.hasAnswer()) {
                submitAnswer();
              } else if (session.verifiedCurrent) {
                advanceQuestion();
              }
            },
          })
        : null;
      session.currentRenderer = renderer;

      const hintCtrl = hintsApi
        ? hintsApi.createHintController({
            slug: qSlug,
            question,
            mode: session.mode,
          })
        : null;
      session.currentHints = hintCtrl;

      async function submitAnswer() {
        if (session.verifiedCurrent || !renderer || !renderer.hasAnswer()) return;
        verifyBtn.disabled = true;
        renderer.setLocked(true);

        const payload = {
          question_id: question.id,
          answer: renderer.getAnswer(),
          mode: session.mode,
        };
        if (question.generator && question.generator.id) {
          payload.generator_id = question.generator.id;
          payload.seed = question.generator.seed;
          payload.params = question.generator.params || {};
        }

        try {
          const resp = await dom.apiFetch(
            `/api/${encodeURIComponent(qSlug)}/verify`,
            {
              method: "POST",
              body: JSON.stringify(payload),
            },
          );
          session.verifiedCurrent = true;
          const ok = Boolean(resp && resp.correct);
          session.results.push({ id: question.id, correct: ok });
          renderer.showResult(resp);
          updateBinaryMatrix(ok);

          dom.clear(feedbackSlot);
          feedbackSlot.hidden = false;
          feedbackSlot.dataset.verdict = ok ? "ok" : "err";
          feedbackSlot.appendChild(
            dom.el(
              "div",
              { className: "eng-feedback-title" },
              ok ? "Správně" : `Chyba — správná odpověď: ${resp.correct_display || ""}`,
            ),
          );
          if (resp && resp.explanation) {
            const expEl = dom.el("div", { className: "eng-question-text" });
            if (katex && typeof katex.renderRichText === "function") {
              katex.renderRichText(expEl, String(resp.explanation));
            } else {
              expEl.textContent = String(resp.explanation);
            }
            feedbackSlot.appendChild(expEl);
          }
          if (resp && resp.fsrs) {
            const intervalDays = Number(resp.fsrs.interval_days || 0);
            const intervalLabel =
              intervalDays < 1
                ? `${Math.max(1, Math.round(intervalDays * 1440))} min`
                : `${intervalDays.toFixed(1)} dne`;
            feedbackSlot.appendChild(
              dom.el(
                "div",
                { className: "eng-feedback-meta" },
                `FSRS · stabilita ${Number(resp.fsrs.stability).toFixed(2)} · obtížnost ${Number(resp.fsrs.difficulty).toFixed(1)} · další opakování za ${intervalLabel}${resp.leech ? " · LEECH" : ""}`,
              ),
            );
          }

          dom.announce(ok ? "Správná odpověď" : "Nesprávná odpověď", !ok);

          verifyBtn.hidden = true;
          if (!ok && modeCfg && modeCfg.stopOnFirstError) {
            nextBtn.textContent = "Zobrazit výsledek →";
          }
          nextBtn.hidden = false;
          nextBtn.focus();
        } catch (err) {
          renderer.setLocked(false);
          verifyBtn.disabled = false;
          dom.announce(err && err.message ? err.message : "Ověření se nezdařilo", true);
        }
      }

      function advanceQuestion() {
        const lastRes = session.results[session.results.length - 1];
        if (lastRes && !lastRes.correct && modeCfg && modeCfg.stopOnFirstError) {
          finishSession();
          return;
        }
        session.index += 1;
        renderCurrentQuestion();
      }

      verifyBtn.addEventListener("click", submitAnswer);
      nextBtn.addEventListener("click", advanceQuestion);

      const refBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-secondary btn-sm" },
        "Tahák (F)",
      );
      refBtn.addEventListener("click", () => {
        if (ns.scratchpad && typeof ns.scratchpad.openReference === "function") {
          ns.scratchpad.openReference();
        }
      });

      const scratchBtn = dom.el(
        "button",
        { type: "button", className: "btn btn-secondary btn-sm" },
        "Škrabák (S)",
      );
      scratchBtn.addEventListener("click", () => {
        if (ns.scratchpad && typeof ns.scratchpad.openScratchpad === "function") {
          ns.scratchpad.openScratchpad();
        }
      });

      const actionBar = dom.el(
        "div",
        { className: "eng-action-bar", role: "toolbar", "aria-label": "Akce otázky" },
        dom.el(
          "div",
          { className: "eng-action-secondary" },
          hintCtrl ? hintCtrl.triggerButton : null,
          refBtn,
          scratchBtn,
        ),
        dom.el("div", { className: "eng-action-primary" }, verifyBtn, nextBtn),
      );

      workspace.appendChild(
        dom.el(
          "div",
          { className: "eng-workspace" },
          dom.el(
            "div",
            { className: "eng-header" },
            headerMeta,
            dom.el("div", { className: "eng-header-actions" }, timerEl, exitBtn),
          ),
          dom.el("div", { className: "eng-progress-row" }, progressEl),
          dom.el(
            "article",
            { className: "eng-card" },
            promptEl,
            renderer ? renderer.element : null,
            hintCtrl ? hintCtrl.element : null,
            feedbackSlot,
          ),
          actionBar,
        ),
      );

      if (renderer && typeof renderer.focus === "function") {
        window.requestAnimationFrame(() => renderer.focus());
      }
    }

    async function startMode(modeId, options = {}) {
      if (!workspace) return;
      stopTimer();

      const cleanMode = String(modeId || "blocks").toLowerCase();
      const modeCfg = modesApi ? modesApi.getMode(cleanMode) : { id: cleanMode };
      const topic = options.topic !== undefined ? options.topic : getSelectedTopic();
      const typeFilter =
        options.type !== undefined ? options.type : getSelectedTypeFilter();

      session.mode = cleanMode;
      session.topic = topic;
      session.typeFilter = typeFilter;
      session.index = 0;
      session.results = [];
      session.verifiedCurrent = false;
      session.startedAtMs = Date.now();
      session.studyPhasePending = Boolean(modeCfg && modeCfg.studyPhaseFirst);

      let url;
      if (cleanMode === "quick_drill" && !session.slug) {
        url = `/api/drill/quick?limit=${encodeURIComponent(String(options.limit || 10))}`;
      } else {
        const qs = new URLSearchParams({ mode: cleanMode });
        if (topic && cleanMode !== "exam" && cleanMode !== "fsrs_review" && cleanMode !== "error_log") {
          qs.set("topic", topic);
        }
        if (typeFilter && typeFilter !== "all") {
          qs.set("type", typeFilter);
        }
        if (cleanMode === "blocks") {
          qs.set("limit", String(getBlockSize()));
        }
        url = `/api/${encodeURIComponent(session.slug)}/questions?${qs.toString()}`;
      }

      if (subjectGrid) subjectGrid.hidden = true;
      workspace.hidden = false;
      dom.clear(workspace);
      workspace.appendChild(dom.el("p", { className: "page-subtitle" }, "Načítám otázky..."));

      try {
        const data = await dom.apiFetch(url);
        const questions = Array.isArray(data && data.questions) ? data.questions : [];
        session.questions = questions;
        session.active = true;

        if (questions.length === 0) {
          dom.clear(workspace);
          const backBtn = dom.el(
            "button",
            { type: "button", className: "btn btn-secondary" },
            "Zpět na předmět",
          );
          backBtn.addEventListener("click", closeWorkspace);
          workspace.appendChild(
            dom.el(
              "div",
              { className: "eng-summary" },
              dom.el("h2", {}, "Žádné otázky k procvičení"),
              dom.el(
                "p",
                { className: "page-subtitle" },
                "Pro vybraný filtr nebo režim nejsou momentálně žádné položky.",
              ),
              backBtn,
            ),
          );
          return;
        }

        if (modeCfg && modeCfg.timed) {
          const minutes =
            (data && data.exam_config && Number(data.exam_config.minutes)) || 30;
          session.remainingSec = Math.max(60, Math.round(minutes * 60));
          session.timerId = window.setInterval(() => {
            session.remainingSec -= 1;
            const timerEl = document.getElementById("eng-exam-timer");
            if (timerEl) {
              timerEl.textContent = formatClock(session.remainingSec);
              timerEl.dataset.urgent = session.remainingSec <= 120 ? "1" : "0";
            }
            if (session.remainingSec <= 0) {
              finishSession();
            }
          }, 1000);
        }

        if (session.studyPhasePending) {
          await renderStudyBriefBeforeTest();
        } else {
          renderCurrentQuestion();
        }
      } catch (err) {
        dom.clear(workspace);
        const backBtn = dom.el(
          "button",
          { type: "button", className: "btn btn-secondary" },
          "Zpět",
        );
        backBtn.addEventListener("click", closeWorkspace);
        workspace.appendChild(
          dom.el(
            "div",
            { className: "eng-summary" },
            dom.el("p", { role: "alert" }, err && err.message ? err.message : "Chyba načítání"),
            backBtn,
          ),
        );
      }
    }

    // Wire mode buttons and hero CTA on subject page.
    for (const btn of document.querySelectorAll("[data-start-mode]")) {
      btn.addEventListener("click", () => {
        const mode = btn.dataset.startMode || "blocks";
        startMode(mode);
      });
    }

    // Keyboard shortcuts inside active quiz (1-4 for choice, Enter to submit/advance).
    document.addEventListener("keydown", (event) => {
      if (
        !session.active ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        document.documentElement.dataset.shortcuts === "0" ||
        document.querySelector("dialog[open]")
      ) {
        return;
      }

      if (
        !dom.isEditableTarget(event.target) &&
        session.currentRenderer &&
        typeof session.currentRenderer.handleKey === "function" &&
        !session.verifiedCurrent
      ) {
        if (session.currentRenderer.handleKey(event)) {
          event.preventDefault();
          return;
        }
      }

      if (event.key === "Enter" && !dom.isEditableTarget(event.target)) {
        const primary = workspace
          ? workspace.querySelector(".eng-action-primary button:not([hidden]):not(:disabled)")
          : null;
        if (primary) {
          event.preventDefault();
          primary.click();
        }
      }
    });

    // Auto-start if URL query explicitly specifies ?mode=...
    const params = new URLSearchParams(window.location.search);
    const urlMode = params.get("mode");
    const urlTopic = params.get("topic");
    if (urlTopic) {
      for (const btn of document.querySelectorAll("[data-topic-select]")) {
        btn.setAttribute(
          "aria-pressed",
          btn.dataset.topicSelect === urlTopic ? "true" : "false",
        );
      }
    }
    if (urlMode && workspace && session.slug) {
      startMode(urlMode, { topic: urlTopic || undefined });
    }

    return {
      startMode,
      closeWorkspace,
      getSession: () => ({ ...session }),
    };
  }

  ns.initEngineHost = () => {
    ns.engineHost = createEngineHost();
    return ns.engineHost;
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ns.initEngineHost, { once: true });
  } else {
    ns.initEngineHost();
  }
})();
