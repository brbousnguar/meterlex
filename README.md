<p align="center">
  <img src="frontend/public/brand-mark.svg" width="88" alt="Agentic Spend mark" />
</p>

<h1 align="center">Agentic Spend</h1>

<p align="center">
  Every token your coding tools spend, priced at market rate.<br />
  Subscription vs API — know which one wins.
</p>

---

Agentic Spend reads the session logs your coding tools write to disk, prices every model call at the public API rate, and shows you what the subscription actually costs you versus what you would pay on consumption. The flat monthly fee hides the real usage — this makes it visible.

It is deliberately local. Data stays in a SQLite file on your machine, the stack runs in two Docker containers, and the interface follows the same warm technical notebook style as the rest of the server.

## What it tracks

- **Claude Code** — every `assistant` event from `~/.claude/projects/**/*.jsonl`, with full input / output / cache token breakdown
- **Codex** — per-turn token deltas from `~/.codex/sessions/**/*.jsonl` via `token_count` events; OpenAI Codex endpoint billed against ChatGPT Pro quota
- **Antigravity** — per-conversation SQLite DBs at `~/.gemini/antigravity-cli/conversations/*.db`; token counts extracted from protobuf step payloads, model IDs from `gen_metadata` blobs
- **Ollama** — GLM cloud model calls made through Claude Code, re-attributed by model prefix. Cloud GLM models are priced at official Z.AI rates; only truly local Ollama models (`local/*`) are €0.
- **Copilot CLI** — the agentic `copilot` binary writes per-session token totals (input / output / cached / reasoning) to `~/.copilot/data.db`. The CLI reports `model = 'auto'`, so tokens are priced at a representative `copilot-auto` rate (editable in Prices & settings). In-editor Copilot usage is not exposed locally and is not counted. The CLI retains only recent sessions, so older usage is not recoverable. The flat subscription is tracked via monthly bills seeded in Prices & settings.

Costs are computed as `(input × prompt_rate + output × completion_rate + cache_read × cache_read_rate + cache_write × cache_write_rate) × fx_rate`. Savings are `API cost − subscription` — positive means the subscription wins.

## Start the dashboard

```sh
docker compose up -d
```

| Surface | Address |
| --- | --- |
| Dashboard | `http://localhost:5180` |
| REST API | `http://localhost:8692/api/health` |

The backend bind-mounts your coding-tool session directories **read-only**. Host paths are not hardcoded in `docker-compose.yml` — they come from a property file. Copy the template and adjust for your machine:

```sh
cp .env.example .env   # then edit the three paths in .env
docker compose up -d
```

`docker compose` auto-loads `.env` from the project root. The three variables are `CLAUDE_CODE_PROJECTS`, `CODEX_SESSIONS`, and `AGY_DIR`. If a directory doesn't exist on your machine, leave it set — the mount will be empty and that source simply shows no data. On Windows use forward slashes (`C:/Users/...`); Docker Desktop accepts them.

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
  agentic-spend.db  SQLite database
docker-compose.yml  Two-service stack (backend + nginx-served frontend)
```

Nginx serves the React frontend and proxies `/api/` and `/api/spend/timeseries` to FastAPI. The backend runs an async background loop that re-scans source directories every 60 seconds.

## API surface

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Status, total turns, last ingest timestamp |
| `GET` | `/api/summary` | Per-tool cost summary with subscription comparison |
| `GET` | `/api/spend` | Aggregated spend for a period, with model and project breakdown |
| `GET` | `/api/spend/timeseries` | Bucketed series (daily / monthly / yearly) with per-source splits |
| `GET` | `/api/prices` | All model prices |
| `PATCH` | `/api/prices/{model_id}` | Manual price override |
| `GET/PATCH` | `/api/settings` | FX rate and per-tool subscription amounts |
| `GET` | `/api/bills/{source}` | Per-month manual bills (used for variable Copilot charges) |
| `PATCH` | `/api/bills/{source}/{year_month}` | Set or update a monthly bill |
| `POST` | `/api/scan` | Trigger immediate incremental scan |
| `POST` | `/api/recompute` | Reapply current prices to all history |

## Pricing policy

Seed prices come from public Anthropic, OpenAI, Google, and Z.AI rate cards and are stored in `pricing.py`. Manual overrides via the UI win over seed values. Gemini models without an exact match fall back to the generic flash-tier entry; unrecognised `glm-*` IDs fall back to the GLM-5.2 tier. Cloud GLM models are billed at official Z.AI rates — only truly local Ollama models (`local/*`) and `<synthetic>` events are priced at €0. FX defaults to 0.92 (USD → EUR), editable in settings. After changing a price or the FX rate, hit **Recompute all costs** to reapply to history.

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
