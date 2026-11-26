# UNI Suite (`uni.rutterle.eu`)

Neoficiální studentská studijní platforma a sandbox pro procvičování vysokoškolských předmětů.

## Předměty

- **NUM** — Numerické metody (parametrizované úlohy, tahák, vzorce, Python/R sandbox)
- **PAS** — Pravděpodobnost a statistika (distribuční tabulky, generátor příkladů, vzorce)
- **TZI** — Teoretické základy informatiky (banky otázek, doplňovačky, glosář, automaty)

## Co platforma umí

- **Adaptivní procvičování:** FSRS spaced repetition, Chybovník a 3úrovňové nápovědy.
- **Studijní nástroje:** rychlé hledání (`Ctrl+K`), tahák a vzorce (`F`), škrabák (`S`) a přizpůsobení motivu.
- **Code sandbox:** spouštění kódu v prohlížeči (Tier 1 WASM) a volitelně v izolovaných Docker kontejnerech (Tier 2: R, C# .NET).
- **Hlášení chyb a návrhů:** vestavěný formulář v navigaci a patičce posílá podněty přímo do GitHub Issues přes `POST /api/feedback`.

## Lokální spuštění

Požadavky: Python 3.12+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Vygeneruj `APP_SECRET` a zapiš ho do `.env`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Spuštění vývojového serveru (výchozí `127.0.0.1:5052`):

```bash
.venv/bin/python server.py
```

## Konfigurace (`.env`)

| Proměnná | Popis | Výchozí |
| --- | --- | --- |
| `APP_SECRET` | Povinný náhodný tajný klíč (min. 32 znaků) | — |
| `UNI_HOST` / `UNI_PORT` | Adresa a port Uvicorn serveru | `127.0.0.1` / `5052` |
| `UNI_DB_PATH` | Cesta k SQLite databázi (WAL) | `uni.db` |
| `ADMIN_USERNAME` / `HASHED_PASSWORD` | Volitelný seed admin účtu (Argon2id hash) | vypnuto |
| `GITHUB_ISSUES_TOKEN` | Fine-grained PAT (`Issues: Read and write`) pro formulář návrhů/chyb | vypnuto |
| `GITHUB_ISSUES_REPO` | Cílový repozitář pro GitHub Issues | `Richieee-0006/uni-suite` |
| `SANDBOX_DOCKER_ENABLED` | Povolí Tier 2 Docker sandbox | `0` |

## Testy a kontroly

```bash
TESTING=1 .venv/bin/pytest tests/ -q
ruff check --select S,E,F server.py db.py webapp/ scripts/
```

Podrobnosti k architektuře, bezpečnostním hlavičkám (CSP, Trusted Types, CSRF) a struktuře repozitáře najdeš v [ARCHITECTURE.md](ARCHITECTURE.md).
