/**
 * StudyModeRegistry — composable individual study modes (zero PvP/Arena).
 */
(() => {
  "use strict";

  const MODES = new Map();

  function registerMode(config) {
    if (!config || !config.id) return;
    MODES.set(String(config.id).toLowerCase(), Object.freeze({ ...config }));
  }

  function getMode(id) {
    return MODES.get(String(id || "practice").toLowerCase()) || MODES.get("practice");
  }

  function listModes() {
    return Array.from(MODES.values());
  }

  registerMode({
    id: "practice",
    label: "Procvičování",
    description: "Volné procvičování s okamžitou zpětnou vazbou a nápovědami.",
    hintsLocked: false,
    timed: false,
    stopOnFirstError: false,
    studyPhaseFirst: false,
    saveScoreOnFinish: false,
    crossSubject: false,
  });

  registerMode({
    id: "blocks",
    label: "Po blocích",
    description: "Rozdělení okruhu do soustředěných bloků po N otázkách.",
    hintsLocked: false,
    timed: false,
    stopOnFirstError: false,
    studyPhaseFirst: false,
    saveScoreOnFinish: false,
    crossSubject: false,
  });

  registerMode({
    id: "study_then_test",
    label: "Studuj, pak testuj",
    description: "Nejprve krátký přehled a vzorce okruhu, poté kontrolní blok.",
    hintsLocked: false,
    timed: false,
    stopOnFirstError: false,
    studyPhaseFirst: true,
    saveScoreOnFinish: false,
    crossSubject: false,
  });

  registerMode({
    id: "exam",
    label: "Ostrý test",
    description: "Časovaná simulace zkoušky se zamčenými nápovědami a uložením skóre.",
    hintsLocked: true,
    timed: true,
    stopOnFirstError: false,
    studyPhaseFirst: false,
    saveScoreOnFinish: true,
    crossSubject: false,
  });

  registerMode({
    id: "sudden_death",
    label: "Náhlá smrt",
    description: "Jedna chyba končí pokus — nápovědy zamčeny na serveru.",
    hintsLocked: true,
    timed: false,
    stopOnFirstError: true,
    studyPhaseFirst: false,
    saveScoreOnFinish: true,
    crossSubject: false,
  });

  registerMode({
    id: "fsrs_review",
    label: "FSRS opakování",
    description: "Adaptivní opakování podle křivky zapomínání (stability/difficulty).",
    hintsLocked: false,
    timed: false,
    stopOnFirstError: false,
    studyPhaseFirst: false,
    saveScoreOnFinish: false,
    crossSubject: false,
  });

  registerMode({
    id: "error_log",
    label: "Chybovník",
    description: "Cílený trénink chybně zodpovězených otázek a leech karet.",
    hintsLocked: false,
    timed: false,
    stopOnFirstError: false,
    studyPhaseFirst: false,
    saveScoreOnFinish: false,
    crossSubject: false,
  });

  registerMode({
    id: "quick_drill",
    label: "5min drill slabých míst",
    description: "Prioritní mix leech karet, prošlých FSRS karet a posledních chyb.",
    hintsLocked: false,
    timed: false,
    stopOnFirstError: false,
    studyPhaseFirst: false,
    saveScoreOnFinish: false,
    crossSubject: true,
  });

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.modes = {
    registerMode,
    getMode,
    listModes,
  };
})();
