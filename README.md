<p align="center">
  <img src="frontend/public/brand-mark.svg" width="88" alt="Meterlex mark" />
</p>

<h1 align="center">Meterlex</h1>

<p align="center">
  Local-first cost intelligence for AI coding tools.<br />
  Every token priced. Every subscription measured.
</p>

<p align="center">
  <a href="actions/workflows/ci.yml"><img alt="CI" src="actions/workflows/ci.yml/badge.svg" /></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
  <img alt="Project status: active" src="https://img.shields.io/badge/status-active-2ea44f.svg" />
</p>

---

Meterlex reads the session logs your coding tools write to disk, prices every
model call at the public API rate, and shows you what the subscription actually
costs you versus what you would pay on consumption. The flat monthly fee hides
the real usage — Meterlex makes it visible.

It is deliberately local. Data stays in a SQLite file on your machine, the
stack runs in two Docker containers, and the source logs are mounted read-only.

> **Project status:** actively developed and suitable for personal/local use.
> Log formats and model rate cards can change, so verify figures before using
> them for accounting. The API is unauthenticated and must not be exposed
> directly to the public internet.

## What it tracks

- **Claude Code** — every `assistant` event from `~/.claude/projects/**/*.jsonl`, with full input / output / cache token breakdown
- **Codex** — per-turn token deltas from `~/.codex/sessions/**/*.jsonl` via `token_count` events; OpenAI Codex endpoint billed against ChatGPT Pro quota
- **Antigravity** — per-conversation SQLite DBs at `~/.gemini/antigravity-cli/conversations/*.db`; token counts extracted from protobuf step payloads, model IDs from `gen_metadata` blobs
- **Ollama** — GLM cloud model calls made through Claude Code, re-attributed by model prefix. Cloud GLM models are priced at official Z.AI rates; only truly local Ollama models (`local/*`) are €0.
- **Copilot CLI** — the agentic `copilot` binary writes per-session token totals (input / output / cached / reasoning) to `~/.copilot/data.db`. The CLI reports `model = 'auto'`, so tokens are priced at a representative `copilot-auto` rate (editable in Prices & settings). In-editor Copilot usage is not exposed locally and is not counted. The CLI retains only recent sessions, so older usage is not recoverable. Variable subscription charges can be entered as monthly bills in Prices & settings.

Costs are computed as `(input × prompt_rate + output × completion_rate + cache_read × cache_read_rate + cache_write × cache_write_rate) × fx_rate`. Savings are `API cost − subscription` — positive means the subscription wins.

## Quick start

### Prerequisites

- Docker Desktop or Docker Engine with Docker Compose v2
- At least one supported coding tool with local session data
- Ports `5180` and `8692` available on the host

Clone the repository, create the local configuration, and start the stack:

```sh
git clone <repository-url>
cd meterlex
cp .env.example .env
# Edit .env for your machine, then:
docker compose up -d
```

| Surface | Address |
| --- | --- |
| Dashboard | `http://localhost:5180` |
| Health check | `http://localhost:8692/api/health` |
| Interactive API docs | `http://localhost:8692/docs` |

Check startup health with `docker compose ps` and follow logs with
`docker compose logs -f`. Stop the application with `docker compose down`.
Usage data remains in `data/meterlex.db`; remove that file only when you
intentionally want a fresh local database.

### Configuration

Docker Compose automatically reads `.env` from the repository root. Every host
directory is mounted into the backend container read-only.

| Variable | Typical source | Purpose |
| --- | --- | --- |
| `CLAUDE_CODE_PROJECTS` | `~/.claude/projects` | Claude Code JSONL sessions |
| `CODEX_SESSIONS` | `~/.codex/sessions` | Codex JSONL sessions |
| `AGY_DIR` | `~/.gemini/antigravity-cli` | Antigravity conversation databases |
| `COPILOT_CLI_DIR` | `~/.copilot` | Copilot CLI session database |

Use absolute host paths. On Windows, use forward slashes such as
`C:/Users/your-username/...`; Docker Desktop accepts them. If a tool is not
installed, point its variable to an existing empty directory.

## How it is put together

```text
backend/
  main.py           FastAPI application and REST API
  ingest.py         Incremental scanner — Claude Code, Codex, Antigravity (async loop)
  pricing.py        Price resolution: seed prices → manual overrides → free fallback
  models.py         SQLModel schema (UsageTurn, ModelPrice, Setting, ManualBill)
  database.py       Engine and session helpers
frontend/
  src/
    components/     Dashboard, ToolDetail, PricesSettings, InfoTip
  public/
    brand-mark.svg  Identity mark and favicon
data/
  meterlex.db       SQLite database
docker-compose.yml  Two-service stack (backend + nginx-served frontend)
```

Nginx serves the React frontend and proxies `/api/` and `/api/spend/timeseries` to FastAPI. The backend runs an async background loop that re-scans source directories every 60 seconds.

## Tests and continuous integration

Backend tests use isolated in-memory SQLite databases and synthetic log events:

```sh
python -m pip install -r backend/requirements-dev.txt
python -m pytest
```

Build the React application with:

```sh
cd frontend
npm ci
npm run build
```

Maestro browser journeys in `.maestro/` exercise initial dashboard rendering,
tool navigation, and the prices/settings screen using accessible selectors.
With the Docker stack running, execute them using
`maestro test --platform web --headless .maestro`.
GitHub Actions runs backend tests, the production frontend build, repository
hygiene checks, dependency audit, Docker Compose validation, and Maestro flow
YAML validation for pull requests targeting `main` and pushes to `main`.
Maestro's desktop-web support is currently beta, so the full browser journeys
remain an explicit local check instead of a required merge gate.

For a local development server without Docker:

```sh
# Terminal 1
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
cd backend && uvicorn main:app --reload --port 8692

# Terminal 2
cd frontend
npm ci
npm run dev
```

On Windows PowerShell, activate the environment with
`.venv\Scripts\Activate.ps1`. The Vite development server proxies `/api` to the
backend on port `8692`.

## Privacy and security

- Source directories are mounted read-only, but derived rows may retain project
  names and local file paths.
- Runtime databases, environment files, coverage output, and browser artifacts
  are ignored by Git.
- Use synthetic fixtures in issues and pull requests; never upload raw prompts,
  session logs, invoices, credentials, or employer/client data.
- This project has no authentication. Keep ports `5180` and `8692` on a trusted
  local network and review [SECURITY.md](SECURITY.md) before deployment.

Contributions are covered by the [MIT License](LICENSE); see
[CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.

## Known limitations

- Results depend on undocumented local log formats that tool vendors may
  change without notice.
- Historical data is limited to what each local tool still retains.
- Model aliases and bundled/automatic model selection may require approximate
  pricing; rates are overridden at the model-prices service, not in this UI.
- The application does not authenticate users or encrypt its SQLite database.
- Figures are estimates for engineering insight, not invoices or accounting
  records.

## Roadmap

The near-term priorities are parser fixtures for more log-format versions,
stronger end-to-end coverage, configurable retention, and easier export of
aggregated data. Feature requests and parser samples are welcome as GitHub
issues, but samples must be synthetic and contain no prompts or identifying
paths.

## API surface

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Status, total turns, last ingest timestamp |
| `GET` | `/api/summary` | Per-tool cost summary with subscription comparison |
| `GET` | `/api/spend` | Aggregated spend for a period, with model and project breakdown |
| `GET` | `/api/spend/timeseries` | Bucketed series (daily / monthly / yearly) with per-source splits |
| `GET` | `/api/prices` | All model prices (mirrored from the model-prices service) |
| `POST` | `/api/prices/mirror-pull` | Pull latest rates from model-prices and recompute all costs |
| `GET/PATCH` | `/api/settings` | FX rate and per-tool subscription amounts |
| `GET` | `/api/bills/{source}` | Per-month manual bills (used for variable Copilot charges) |
| `PATCH` | `/api/bills/{source}/{year_month}` | Set or update a monthly bill |
| `POST` | `/api/scan` | Trigger immediate incremental scan |
| `POST` | `/api/recompute` | Reapply current prices to all history |

## Pricing policy

The rate card itself lives in [model-prices](https://github.com/brbousnguar/model-prices), a small shared service also used by openclaw-spend, and is mirrored into Meterlex's local `model_prices` table (read-only here — edit rates there, not in this app). A weekly cron pulls the latest rates into both apps every Monday at 05:00 and recomputes historical costs; use **Recompute all costs** to do that on demand. Gemini models without an exact match fall back to the generic flash-tier entry; unrecognised `glm-*` IDs fall back to the GLM-5.2 tier; unrecognised `gpt-5*` IDs fall back to the base GPT-5 tier — this fallback logic itself stays local to Meterlex. Cloud GLM models are billed at official Z.AI rates — only truly local Ollama models (`local/*`) and `<synthetic>` events are priced at €0. FX defaults to 0.92 (USD → EUR), editable in settings.

## Visual identity

The mark is a terminal chevron and cursor alongside rising spend bars inside a rounded chip. The chevron references the coding-tool context; the ascending bars are the cost trend; the dashed line connects their peaks.

| Role | Color | Use |
| --- | --- | --- |
| Parchment | `#f7f0e3` | Canvas |
| Kraft | `#ede3cc` | Surfaces |
| Espresso | `#2d1a08` | Type and structure |
| Burnt orange | `#c8620a` | Primary action and identity |
| Warm olive | `#7a9448` | Success / savings |
| Golden amber | `#c89020` | Warning / Ollama accent |

Display copy uses **Chakra Petch**. Values, tokens, and annotations use **IBM Plex Mono**.

---

<p align="center"><code>SCAN / PRICE / TRACK</code></p>
