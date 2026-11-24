# UNI Suite (`uni.rutterle.eu`) — Architecture

Unified, modular, mobile-first study platform and polyglot code sandbox for KI UJEP subjects (`KI/NUM`, `KI/PAS`, `KI/TIN`, and future `APR`, `URDB`, `NoSQL`, `OONV`).

## 1. Repository Layout

```text
uni-suite/
├── server.py                      # Uvicorn entrypoint (127.0.0.1:5052)
├── db.py                          # SQLite WAL (Argon2id + bcrypt upgrade, session_version, FSRS/Chybovník, preferences)
├── webapp/
│   ├── main.py                    # FastAPI app (openapi disabled), strict CSP/Trusted Types + HSTS, /static -> ui/public
│   ├── config.py                  # Fail-fast .env loader (APP_SECRET required, no default admin hash)
│   ├── cookie_utils.py            # Signed HttpOnly SameSite=Lax cookies
│   ├── sessions.py                # IP/UA binding + DB session_version revocation
│   ├── csrf.py                    # Double-submit CSRF (TESTING=1 only, never User-Agent bypass)
│   ├── preferences.py             # Strict allowlist validator for user customizer preferences
│   ├── subjects.py                # Declarative subject registry + role-aware projection (strips provider/admin drafts)
│   ├── engine/
│   │   └── fsrs.py                # Native FSRS spaced repetition + Chybovník (error log & leech detection)
│   ├── generators/                # Deterministic seeded parameterized problem generators (NUM, PAS)
│   ├── verifiers/                 # choice, exact, numeric_eps, recall_lev1, code_sandbox
│   ├── tools/                     # glossary, formulas, numeric_lab, dist_tables, automata
│   ├── sandbox/                   # Pluggable LanguageDriver (Tier 2 ephemeral Docker drivers: R, C# .NET)
│   └── routers/
│       ├── portal.py              # /, /predmet/{slug}, /predmet/{slug}/code, /api/portal, /api/search
│       ├── auth.py                # /login, /register, /trial, POST /logout
│       ├── preferences.py         # GET/PUT /api/preferences
│       ├── study.py               # /api/{slug}/{sources,questions,verify,progress,fsrs,errors,drill,tools}
│       ├── sandbox.py             # /api/sandbox/run (rate-limited Tier 2 runner)
│       ├── admin.py               # Isolated /admin, /admin/assets/*, /api/admin/* (404 anti-enumeration)
│       └── internal.py            # Loopback-only /internal/subjects/{slug}/bundle (INTERNAL_SYNC_TOKEN)
├── subjects/
│   ├── registry.json              # Ordered subject catalog
│   ├── num/                       # KI/NUM (meta.json, banky/, glosar/, formulas.json)
│   ├── pas/                       # KI/PAS (meta.json, banky/, glosar/, formulas.json)
│   └── tzi/                       # KI/TIN (migrated TZI banks, doplnovacka, glosar, automata)
├── ui/
│   ├── templates/                 # Jinja2 auto-escaped HTML5 SSR templates (outside /static)
│   ├── admin/                     # Isolated admin HTML/CSS/JS (never mounted on /static)
│   └── public/                    # Mounted at /static (CSS3 @layer, DOM-safe JS, WASM workers, KaTeX vendor, PWA)
├── scripts/                       # Obsidian sync, TZI upstream sync, TZI DB migration
├── docker/                        # Ephemeral Tier 2 sandbox Dockerfiles (C# .NET)
└── tests/                         # Pytest API, security, HTML5/CSS3 validation, Playwright + axe-core E2E
```

## 2. Core Principles

- **HTML5 & CSS3+ Standards:** Valid WHATWG HTML5 Living Standard (`html-validate` 0 errors) and W3C CSS3+ (`@layer`, logical properties, `@container`, Grid/`subgrid`, `color-scheme`, `forced-colors`, `stylelint` 0 errors).
- **Mobile-First (`320 px → 1440 px+`):** Swipeable `popover="manual"` left rail on narrow screens, `>= 44 px` touch targets on coarse pointers, `safe-area-inset-*`, zero horizontal page overflow.
- **Security-by-Design:** `Argon2id` password hashing, `session_version` revocation, double-submit CSRF, zero public admin/draft leakage (`404` anti-enumeration), strict CSP + `require-trusted-types-for 'script'`, zero `innerHTML`, isolated WASM/Docker code execution.
- **Individual Study Focus:** No PvP or competitive arenas — focused purely on mastery (`1/0/·`, FSRS, Chybovník, 3-step progressive hints, parameterized problems, interactive `<canvas>` mini-labs, and browser-native code sandboxes).
