# eToro Trading Agent - Implementation Plan

## Overview

A Python-based **advisory trading agent** that runs twice daily at UK market open (08:00 GMT) and close (16:30 GMT). The agent fetches market data and portfolio state from eToro's public API, analyses price action and sector context, uses an LLM to generate natural language commentary, and produces a **report of recommended actions** - it does not execute trades automatically.

SurrealDB is used as the persistence layer, storing market data, portfolio snapshots, reports, and configuration. The system runs locally for now but is structured to be deployable to AWS in the future.

> **AWS Migration Note:** All infrastructure and persistence decisions should keep future AWS Free Tier deployment in mind. The target is an EC2 t3.micro instance running the agent on a cron schedule, using SurrealDB in **embedded file-based mode** (no separate DB server). This means:
> - The DB connection layer must support remote (WebSocket), embedded file-based, and in-memory modes via the `SURREAL_URL` setting
> - Avoid dependencies on Docker or long-running services in production — the agent should be a single CLI invocation
> - Keep compute and storage minimal (well within Free Tier: 750 hrs/month EC2, 5 GB S3 for backups)
> - No AWS-specific code until migration phase — just keep the architecture compatible

**Asset coverage:** Stocks, crypto, ETFs, and commodities.

---

## 1. Architecture

```
┌───────────────────────────────────────────────────────────┐
│                    Trading Agent (CLI)                      │
│                                                            │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Analysis    │  │  Portfolio    │  │  Report          │  │
│  │  Engine      │  │  Reader      │  │  Generator       │  │
│  │ (trends,     │  │ (sync from   │  │ (LLM commentary  │  │
│  │  sectors)    │  │  eToro)      │  │  + actions)      │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                    │            │
│  ┌──────▼─────────────────▼────────────────────▼─────────┐ │
│  │                 Core Orchestrator                      │ │
│  └──────────┬─────────────────────────┬──────────────────┘ │
│             │                         │                    │
│  ┌──────────▼───────────┐  ┌──────────▼───────────────┐   │
│  │   eToro API Client   │  │   SurrealDB Client       │   │
│  │   (REST only)        │  │                           │   │
│  └──────────────────────┘  └───────────────────────────┘   │
│             │                         │                    │
└─────────────┼─────────────────────────┼────────────────────┘
              │                         │
 ┌────────────▼──────────┐  ┌───────────▼──────────────┐
 │  eToro Public API      │  │  SurrealDB               │
 │  (REST)                │  │  (local Docker)           │
 └────────────────────────┘  └──────────────────────────┘
```

### Component Responsibilities

| Component | Purpose |
|---|---|
| **Core Orchestrator** | Runs the evaluation pipeline end-to-end, CLI entry point |
| **eToro API Client** | Authenticated HTTP client for eToro REST API (read-only for MVP) |
| **SurrealDB Client** | Data access layer for all persistence |
| **Analysis Engine** | Evaluates price action, trends, and sector context for each tracked instrument |
| **Portfolio Reader** | Fetches current portfolio state and P&L from eToro |
| **Report Generator** | Combines analysis into a structured report with LLM-generated commentary |

---

## 2. eToro API Integration

### Authentication

eToro uses a **header-based key system** (no OAuth):

| Header | Description |
|---|---|
| `x-request-id` | Unique UUID v4 per request |
| `x-api-key` | Public API key |
| `x-user-key` | User key (requires SMS verification to generate) |

**Setup steps:**
1. Log into eToro account
2. Navigate to Settings > Trading > API Key Management
3. Create a new key with **Read** permissions (Write not needed for MVP)
4. Complete SMS verification
5. Copy both the API key and User key

### Base URL

- **REST:** `https://public-api.etoro.com/api/v1`
- No WebSocket needed for MVP

### API Endpoints Used (MVP - Read Only)

#### Market Data
| Endpoint | Purpose |
|---|---|
| **Instrument search** | Resolve tickers (e.g. `AAPL`, `BTC`) to eToro instrument IDs |
| **OHLCV history** | Historical candle data (daily timeframe) |
| **Closing prices** | Historical closing prices across instruments |
| **Bid/Ask prices** | Current pricing and conversion rates |
| **Instrument metadata** | Asset class, exchange, industry classification |

#### Portfolio (Read Only)
| Endpoint | Purpose |
|---|---|
| **Portfolio retrieval** | Current open positions with P&L |
| **Order information** | Pending order details |
| **Trading history** | Historical closed trades |

#### Not Used in MVP
- Trading endpoints (open/close positions) - future phase
- WebSocket streaming - not needed, runs twice daily
- Social/discovery APIs - not relevant to MVP

### Client Design Principles
- Synchronous `httpx` client (no async needed for batch runs)
- Automatic retry with exponential backoff (3 attempts)
- Unique `x-request-id` per request
- Response validation with Pydantic models
- Rate limit awareness (track response headers, back off if needed)
- All API errors logged with full context to SurrealDB

---

## 3. SurrealDB Data Model

We use SurrealDB's document model for structured records, graph relations for linking analysis to reports, and events for automatic audit logging.

### Schema

```surql
-- ============================================================
-- INSTRUMENTS
-- ============================================================
DEFINE TABLE instrument SCHEMAFULL;
DEFINE FIELD etoro_id        ON instrument TYPE int;
DEFINE FIELD symbol          ON instrument TYPE string;
DEFINE FIELD name            ON instrument TYPE string;
DEFINE FIELD asset_class     ON instrument TYPE string;       -- stock, crypto, etf, commodity
DEFINE FIELD exchange        ON instrument TYPE option<string>;
DEFINE FIELD industry        ON instrument TYPE option<string>;
DEFINE FIELD is_active       ON instrument TYPE bool          DEFAULT true;
DEFINE FIELD metadata        ON instrument TYPE option<object>;
DEFINE FIELD updated_at      ON instrument TYPE datetime      DEFAULT time::now();
DEFINE INDEX idx_symbol      ON instrument FIELDS symbol      UNIQUE;
DEFINE INDEX idx_etoro_id    ON instrument FIELDS etoro_id    UNIQUE;

-- ============================================================
-- OHLCV CANDLES
-- ============================================================
DEFINE TABLE candle SCHEMAFULL;
DEFINE FIELD instrument      ON candle TYPE record<instrument>;
DEFINE FIELD timeframe       ON candle TYPE string;            -- 1d, 1w
DEFINE FIELD open            ON candle TYPE float;
DEFINE FIELD high            ON candle TYPE float;
DEFINE FIELD low             ON candle TYPE float;
DEFINE FIELD close           ON candle TYPE float;
DEFINE FIELD volume          ON candle TYPE option<float>;
DEFINE FIELD timestamp       ON candle TYPE datetime;
DEFINE INDEX idx_candle_lookup ON candle FIELDS instrument, timeframe, timestamp UNIQUE;

-- ============================================================
-- PORTFOLIO SNAPSHOTS
-- ============================================================
DEFINE TABLE portfolio_snapshot SCHEMAFULL;
DEFINE FIELD total_value     ON portfolio_snapshot TYPE float;
DEFINE FIELD cash_available  ON portfolio_snapshot TYPE float;
DEFINE FIELD open_positions  ON portfolio_snapshot TYPE int;
DEFINE FIELD total_pnl       ON portfolio_snapshot TYPE float;
DEFINE FIELD positions       ON portfolio_snapshot TYPE array;
DEFINE FIELD positions.*     ON portfolio_snapshot FLEXIBLE TYPE object;
DEFINE FIELD run_type        ON portfolio_snapshot TYPE string; -- market_open, market_close
DEFINE FIELD captured_at     ON portfolio_snapshot TYPE datetime DEFAULT time::now();

-- ============================================================
-- ANALYSIS RESULTS (per instrument per run)
-- ============================================================
DEFINE TABLE analysis SCHEMAFULL;
DEFINE FIELD instrument      ON analysis TYPE record<instrument>;
DEFINE FIELD run_id          ON analysis TYPE string;           -- groups all analyses from one run
DEFINE FIELD trend           ON analysis TYPE string;           -- bullish, bearish, neutral
DEFINE FIELD trend_strength  ON analysis TYPE float;            -- 0.0 to 1.0
DEFINE FIELD price_action    ON analysis FLEXIBLE TYPE object;           -- key price levels, patterns
DEFINE FIELD sector_context  ON analysis FLEXIBLE TYPE option<object>;   -- sector performance, rotation
DEFINE FIELD raw_data        ON analysis FLEXIBLE TYPE object;           -- snapshot of input data used
DEFINE FIELD created_at      ON analysis TYPE datetime          DEFAULT time::now();

-- ============================================================
-- REPORTS (the final output)
-- ============================================================
DEFINE TABLE report SCHEMAFULL;
DEFINE FIELD run_id          ON report TYPE string;
DEFINE FIELD run_type        ON report TYPE string;             -- market_open, market_close
DEFINE FIELD portfolio_snapshot ON report TYPE record<portfolio_snapshot>;
DEFINE FIELD recommendations ON report TYPE array;              -- array of action recommendations
DEFINE FIELD recommendations.* ON report FLEXIBLE TYPE object;
DEFINE FIELD commentary      ON report TYPE string;             -- LLM-generated market commentary
DEFINE FIELD summary         ON report TYPE string;             -- brief headline summary
DEFINE FIELD report_markdown ON report TYPE string;             -- full rendered markdown report
DEFINE FIELD created_at      ON report TYPE datetime            DEFAULT time::now();
DEFINE INDEX idx_run_id      ON report FIELDS run_id            UNIQUE;

-- ============================================================
-- RECOMMENDATIONS (individual actions within a report)
-- ============================================================
DEFINE TABLE recommendation SCHEMAFULL;
DEFINE FIELD report          ON recommendation TYPE record<report>;
DEFINE FIELD instrument      ON recommendation TYPE record<instrument>;
DEFINE FIELD action          ON recommendation TYPE string;     -- buy, sell, hold, reduce, increase
DEFINE FIELD conviction      ON recommendation TYPE string;     -- high, medium, low
DEFINE FIELD reasoning       ON recommendation TYPE string;
DEFINE FIELD analysis        ON recommendation TYPE record<analysis>;
DEFINE FIELD created_at      ON recommendation TYPE datetime    DEFAULT time::now();

-- ============================================================
-- AGENT RUN LOG (audit trail)
-- ============================================================
DEFINE TABLE run_log SCHEMAFULL;
DEFINE FIELD run_id          ON run_log TYPE string;
DEFINE FIELD run_type        ON run_log TYPE string;            -- market_open, market_close
DEFINE FIELD status          ON run_log TYPE string;            -- started, completed, failed
DEFINE FIELD instruments_analysed ON run_log TYPE int;
DEFINE FIELD recommendations_made ON run_log TYPE int;
DEFINE FIELD errors          ON run_log TYPE option<array>;
DEFINE FIELD errors.*        ON run_log FLEXIBLE TYPE object;
DEFINE FIELD duration_ms     ON run_log TYPE option<int>;
DEFINE FIELD started_at      ON run_log TYPE datetime           DEFAULT time::now();
DEFINE FIELD completed_at    ON run_log TYPE option<datetime>;

-- ============================================================
-- CONFIGURATION
-- ============================================================
DEFINE TABLE config SCHEMAFULL;
DEFINE FIELD key             ON config TYPE string;
DEFINE FIELD value           ON config TYPE object;
DEFINE FIELD updated_at      ON config TYPE datetime            DEFAULT time::now();
DEFINE INDEX idx_config_key  ON config FIELDS key               UNIQUE;
```

### Why SurrealDB

| Need | SurrealDB Feature |
|---|---|
| Store OHLCV candles efficiently | Indexed compound fields, range queries on timestamps |
| Link analyses to recommendations to reports | Record references (`record<report>`) provide typed foreign keys |
| Track every run | `run_log` table with structured audit data |
| Flexible analysis output | `object` fields store variable-shape data within schemafull tables |
| Query historical reports | SurrealQL with datetime filtering |
| Future: graph queries | Can add `RELATE` edges later to model complex relationships |
| Single dependency | One database handles all persistence needs |

---

## 4. Project Structure

```
etoro-trading-agent/
├── pyproject.toml                  # Dependencies, scripts, project metadata
├── .env.example                    # Template: API keys, DB config, LLM config
├── PLAN.md                         # This file
├── AGENTS.md                       # Guidelines for AI agents working on this repo
│
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── main.py                 # CLI entry point: parse args, run pipeline
│       ├── config.py               # Pydantic settings from .env
│       ├── orchestrator.py         # Pipeline coordinator: data -> analysis -> report
│       │
│       ├── etoro/
│       │   ├── __init__.py
│       │   ├── client.py           # HTTP client, auth headers, retry logic
│       │   ├── market_data.py      # Instrument search, OHLCV, prices
│       │   ├── portfolio.py        # Portfolio retrieval, trading history
│       │   └── models.py           # Pydantic models for API responses
│       │
│       ├── db/
│       │   ├── __init__.py
│       │   ├── connection.py       # SurrealDB connection lifecycle
│       │   ├── schema.py           # Schema initialisation (SurrealQL above)
│       │   ├── instruments.py      # Instrument CRUD
│       │   ├── candles.py          # OHLCV storage/retrieval
│       │   ├── snapshots.py        # Portfolio snapshot storage
│       │   └── reports.py          # Report and recommendation storage
│       │
│       ├── analysis/
│       │   ├── __init__.py
│       │   ├── price_action.py     # Trend detection, key levels, momentum
│       │   └── sector.py           # Sector/asset class context and rotation
│       │
│       ├── reporting/
│       │   ├── __init__.py
│       │   ├── generator.py        # Assemble report from analyses + portfolio
│       │   ├── llm.py              # LLM client for generating commentary
│       │   └── formatter.py        # Render report as markdown + terminal output
│       │
│       └── utils/
│           ├── __init__.py
│           └── logging.py          # Structured logging setup
│
├── tests/
│   ├── conftest.py                 # Shared fixtures: mock API, test DB
│   ├── test_etoro_client.py        # eToro client auth, retry, error handling
│   ├── test_market_data.py         # Market data fetching and parsing
│   ├── test_portfolio.py           # Portfolio sync and snapshot
│   ├── test_analysis.py            # Price action and sector analysis
│   ├── test_report_generator.py    # Report assembly and formatting
│   └── test_db.py                  # SurrealDB schema and CRUD operations
│
├── reports/                        # Generated report files (gitignored)
│   └── .gitkeep
│
├── scripts/
│   ├── init_db.py                  # Apply schema to a fresh SurrealDB instance
│   └── backfill_candles.py         # One-off: fetch and store historical OHLCV data
│
└── docker-compose.yml              # SurrealDB container for local development
```

---

## 5. Key Dependencies

| Package | Purpose |
|---|---|
| `httpx` | HTTP client for eToro REST API |
| `surrealdb` | Official SurrealDB Python SDK |
| `pydantic` | Settings, data validation, API response models |
| `pydantic-settings` | Load config from `.env` |
| `pandas` | Data manipulation for price analysis |
| `structlog` | Structured JSON logging |
| `openai` (or `anthropic`) | LLM API client for generating market commentary |
| `pytest` | Testing framework |
| `pytest-httpx` | Mock HTTP responses in tests |
| `rich` | Terminal output formatting for reports |

### Planned Dependencies (not yet in `pyproject.toml`)

| Package | Introduced In | Purpose |
|---|---|---|
| `langchain` / `langgraph` | Step 13 | Agentic orchestration and ReAct agent framework |
| `mcp` | Step 15 | Model Context Protocol SDK for exposing agent skills as tools |

---

## 6. Agent Behaviour: The Run Pipeline

The agent runs as a **single CLI invocation**, not a long-running daemon. It is triggered externally (e.g. Windows Task Scheduler, cron, or AWS EventBridge in future).

```
$ python -m agent.main --run-type market_open

┌───────────────────────────────────────────────────────────┐
│                      RUN PIPELINE                          │
│                                                            │
│  1. INITIALISE                                             │
│     └─ Generate run_id (UUID)                              │
│     └─ Create run_log entry (status: started)              │
│     └─ Load config (tracked instruments, LLM settings)     │
│                                                            │
│  2. FETCH MARKET DATA                                      │
│     └─ For each tracked instrument:                        │
│        └─ Fetch latest OHLCV candles (daily)               │
│        └─ Fetch current bid/ask price                      │
│        └─ Store candles in SurrealDB                       │
│                                                            │
│  3. FETCH PORTFOLIO                                        │
│     └─ Get current positions + P&L from eToro              │
│     └─ Save portfolio snapshot to SurrealDB                │
│                                                            │
│  4. ANALYSE                                                │
│     └─ For each tracked instrument:                        │
│        └─ Run price action analysis (trend, momentum,      │
│           key levels, recent patterns)                     │
│        └─ Run sector/market context analysis               │
│        └─ Store analysis results in SurrealDB              │
│                                                            │
│  5. GENERATE REPORT                                        │
│     └─ Combine portfolio state + all analyses              │
│     └─ Send to LLM for:                                    │
│        └─ Market commentary (plain English)                │
│        └─ Per-position assessment                          │
│        └─ Recommended actions (buy/sell/hold/reduce)       │
│     └─ Store report in SurrealDB                           │
│                                                            │
│  6. OUTPUT                                                 │
│     └─ Print report to terminal (via rich)                 │
│     └─ Save report as markdown to reports/ directory       │
│     └─ Update run_log (status: completed)                  │
│                                                            │
└───────────────────────────────────────────────────────────┘
```

### Report Contents

Each report includes:

1. **Summary headline** - one-line overview of market conditions
2. **Portfolio overview** - total value, daily P&L, cash available, number of positions
3. **Per-position commentary** - for each open position:
   - Current P&L and % change
   - Trend assessment (bullish / bearish / neutral)
   - Key price levels (support, resistance)
   - Sector context
4. **Recommended actions** - specific suggestions with conviction level:
   - `BUY` / `SELL` / `HOLD` / `REDUCE` / `INCREASE`
   - Reasoning for each recommendation
5. **Watchlist highlights** - notable moves in tracked instruments not currently held
6. **Market context** - broader market and sector commentary

### Run Schedule

| Run | Time (Europe/London) | Purpose |
|---|---|---|
| `market_open` | 08:00 | Morning briefing: overnight moves, day ahead outlook |
| `market_close` | 16:30 | End-of-day review: session performance, overnight considerations |

**Note:** Times are in Europe/London timezone, which observes daylight saving time (GMT in winter, BST in summer). Schedule the agent accordingly to align with actual UK market hours.

Orchestration is external to the agent - use Windows Task Scheduler locally, AWS EventBridge + Lambda/ECS in future.

---

## 7. Roadmap

Each step is designed to be independently testable before moving on. We start with eToro API integration and build incrementally.

### Step 1: Project Scaffolding
- Create `pyproject.toml` with all dependencies
- Create directory structure (as shown in section 4)
- Create `.env.example` with placeholder values
- Create `docker-compose.yml` for SurrealDB
- Set up `pytest` configuration
- Verify: `pip install -e ".[dev]"` and `pytest` both succeed (with zero tests)

**Acceptance Criteria:**
- [x] `pyproject.toml` exists with all dependencies from section 5, including `[dev]` extras
- [x] Full directory structure under `src/agent/` matches section 4 (all `__init__.py` files present)
- [x] `.env.example` contains all variables from section 8 with placeholder values
- [x] `docker-compose.yml` starts SurrealDB successfully with `docker compose up -d`
- [x] `pip install -e ".[dev]"` completes without errors
- [x] `pytest` runs and exits 0 (no tests collected is acceptable)
- [x] `reports/` directory exists with `.gitkeep`, and is gitignored
- [x] No secrets or real API keys are committed

### Step 2: eToro API Client - Authentication
- Implement `etoro/client.py`: base HTTP client with auth headers
- Implement `config.py`: load API keys from `.env`
- Write tests: verify auth headers are set correctly, request IDs are unique UUIDs
- Write tests: verify error handling for 401/403 responses
- **Manual verification**: make a single authenticated request to eToro API and confirm 200 response

**Acceptance Criteria:**
- [x] `EToroClient` class sends `x-api-key`, `x-user-key`, and a unique `x-request-id` UUID on every request
- [x] Config loads all eToro/SurrealDB/LLM settings from `.env` via `pydantic-settings`
- [x] Client retries failed requests up to 3 times with exponential backoff
- [x] 401 and 403 responses raise clear, descriptive exceptions
- [x] All tests pass with `pytest` - no real API calls in tests
- [x] Manual test confirms a 200 response from at least one eToro endpoint

### Step 3: eToro API Client - Market Data
- Implement `etoro/market_data.py`: instrument search, OHLCV fetch, price fetch
- Implement `etoro/models.py`: Pydantic models for API responses
- Write tests with mocked HTTP responses for each endpoint
- **Manual verification**: fetch real OHLCV data for 2-3 instruments, inspect output

**Acceptance Criteria:**
- [x] `search_instruments(query)` returns a list of instruments with `etoro_id`, `symbol`, `name`, `asset_class`
- [x] `get_candles(instrument_id, timeframe)` returns OHLCV data parsed into Pydantic models
- [x] `get_prices(instrument_ids)` returns current bid/ask for each instrument
- [x] All API responses are validated through Pydantic models (invalid data raises, not silently ignored)
- [x] Each endpoint has at least one test with mocked HTTP responses
- [x] Manual test fetches real candle data for a stock, a crypto, and an ETF

### Step 4: eToro API Client - Portfolio
- Implement `etoro/portfolio.py`: portfolio positions, trading history
- Add Pydantic models for portfolio responses
- Write tests with mocked portfolio data
- **Manual verification**: fetch real portfolio state, confirm positions match eToro UI

**Acceptance Criteria:**
- [x] `get_portfolio()` returns current positions with instrument, amount, P&L, open date
- [x] `get_trading_history()` returns closed trades with entry/exit prices and P&L
- [x] Portfolio and history responses are validated through Pydantic models
- [x] Each endpoint has at least one test with mocked HTTP responses
- [x] Manual test confirms position count and instruments match what is shown in the eToro UI

### Step 5: SurrealDB Connection & Schema
- Implement `db/connection.py`: connect to SurrealDB, handle lifecycle
- Implement `db/schema.py`: apply the SurrealQL schema
- Write `scripts/init_db.py`: CLI script to initialise a fresh database
- Write tests: schema applies cleanly, tables exist, indexes are created
- **Manual verification**: `docker compose up -d`, run init script, query tables via SurrealDB CLI

> **AWS awareness:** The connection factory (`get_connection()`) must support three `SURREAL_URL` modes transparently: `ws://` for local Docker dev, `memory` for tests, and `file://` for future AWS embedded deployment. Tests should use in-memory mode so they run without Docker. Upgrade Docker image to SurrealDB v2.x to match the Python SDK.

**Acceptance Criteria:**
- [x] `get_connection()` connects to SurrealDB using env vars and selects the correct namespace/database
- [x] `apply_schema()` executes all SurrealQL from section 3 without errors
- [x] `python scripts/init_db.py` applies schema to a running SurrealDB instance
- [x] Running `init_db.py` twice is idempotent (no errors on re-apply)
- [x] Tests verify all 8 tables exist (`instrument`, `candle`, `portfolio_snapshot`, `analysis`, `report`, `recommendation`, `run_log`, `config`)
- [x] Tests verify indexes are created (`idx_symbol`, `idx_etoro_id`, `idx_candle_lookup`, `idx_run_id`, `idx_config_key`)

### Step 6: SurrealDB Data Layer
- Implement `db/instruments.py`: upsert and query instruments
- Implement `db/candles.py`: store and query OHLCV candles
- Implement `db/snapshots.py`: store and query portfolio snapshots
- Implement `db/reports.py`: store and query reports
- Write tests for each module (insert, query, upsert, edge cases)

**Acceptance Criteria:**
- [x] `instruments.py`: upsert by symbol (insert or update), query by symbol, query by etoro_id, list all
- [x] `candles.py`: bulk insert candles, query by instrument + timeframe + date range, no duplicate insertion
- [x] `snapshots.py`: create snapshot, query latest, query by date range
- [x] `reports.py`: create report with recommendations, query by run_id, query latest, list by date range
- [x] Each module has tests covering insert, query, upsert, and edge cases (empty results, duplicates)
- [x] All tests pass against a real SurrealDB test instance (not mocked)

### Step 7: End-to-End Data Pipeline
- Implement `orchestrator.py` (steps 1-3 of the run pipeline only)
- Wire up: fetch portfolio -> derive instrument IDs from positions -> resolve instrument metadata -> fetch candles -> store all in SurrealDB
- Tracked instruments are derived from the user's current portfolio positions (config table deferred to Step 12)
- Create `scripts/run_pipeline.py` for manual verification with real API credentials
- Write integration tests: mock eToro API, verify data flows through to SurrealDB
- **Manual verification**: run `scripts/run_pipeline.py`, query SurrealDB to confirm data is stored correctly

**Acceptance Criteria:**
- [x] `Orchestrator.run_data_pipeline()` executes steps 1-3 of the run pipeline (init, fetch portfolio, fetch market data)
- [x] Portfolio is fetched first and a snapshot is saved to SurrealDB
- [x] Instrument IDs are extracted from portfolio positions and resolved via the instruments API
- [x] Instruments are upserted in SurrealDB with metadata from the API
- [x] OHLCV candles are fetched for each portfolio instrument and stored in SurrealDB
- [x] A failed instrument fetch does not abort the entire pipeline — errors are logged and returned in the summary
- [x] An empty portfolio (no positions) completes without error
- [x] Integration tests with mocked eToro API confirm data flows end-to-end
- [x] `scripts/run_pipeline.py` runs the pipeline with real credentials and prints a summary

### Step 8: Analysis Engine

Build a modular, extensible analysis engine that evaluates per-instrument price action and groups results by market/exchange.

**Architecture:**

```
analysis/
  types.py             — Typed dataclasses: IndicatorResult, PriceActionResult, SectorGroupResult, AnalysisResult
  registry.py          — Indicator protocol + IndicatorRegistry (register / run_all / run)
  indicators/
    __init__.py         — Registers built-in indicators on import
    trend.py            — TrendIndicator: higher-highs/lows pattern detection
    momentum.py         — MomentumIndicator: rate-of-change over configurable window
    levels.py           — LevelsIndicator: recent swing high/low support & resistance
  price_action.py      — analyse_price_action(): converts candle dicts → DataFrame, runs registry, aggregates
  sector.py            — analyse_sector(): groups instruments by exchange, computes per-group avg return
db/
  analysis.py          — create_analysis(), get_analyses_by_run_id(), get_analysis_for_instrument()
```

**Design Decisions:**
- **Indicator Protocol + Registry** — new indicators are added by implementing a 2-method protocol (`name` property, `analyse(df) → IndicatorResult`) and calling `registry.register()`
- **Pure functions** — every analysis function takes data in and returns typed results; zero API or DB calls
- **pandas DataFrame** — candle lists are converted to DataFrames once at the `price_action` boundary; indicators receive DataFrames
- **Exchange-based grouping** — sector analysis groups by eToro `exchange_id`: US ("5"/"33"), UK ("7"), EU ("38"), Crypto ("8"), Other
- **Orchestrator wiring** — Step 4 added to `run_data_pipeline()` after candle ingestion; results persisted via `db/analysis.py`

**Acceptance Criteria:**
- [x] `IndicatorResult` dataclass has `name`, `signal` (bullish/bearish/neutral), `strength` (0.0–1.0), `details` (dict)
- [x] `IndicatorRegistry` supports `register(indicator)`, `run_all(df)`, `run(name, df)` and is iterable
- [x] Three built-in indicators registered on import: `trend`, `momentum`, `levels`
- [x] `analyse_price_action(candles)` returns `PriceActionResult` with trend, strength, support/resistance, momentum, and per-indicator results
- [x] `analyse_sector(instruments, candle_map)` returns `SectorResult` grouping instruments by exchange with per-group average return
- [x] All analysis functions are pure — no API or DB calls inside them
- [x] `db/analysis.py` provides `create_analysis()`, `get_analyses_by_run_id()`, `get_analysis_for_instrument()`
- [x] Orchestrator `run_data_pipeline()` includes Step 4: analyse each instrument and persist results
- [x] Pipeline summary dict includes `analyses_created` count
- [x] New indicators can be added without modifying existing code (open/closed principle)
- [x] Tests use synthetic candle data with known trends and verify correct classification
- [x] Tests verify registry extension (register a custom indicator, confirm it runs)
- [x] DB tests use in-memory SurrealDB and verify CRUD round-trips
- [x] `scripts/run_pipeline.py` report includes analysis results section

### Step 9: LLM Commentary
- Implement `reporting/llm.py`: send structured analysis data to LLM, receive natural language commentary
- Design the LLM prompt: portfolio state + analysis data -> market commentary + recommendations
- Implement structured output parsing (LLM returns JSON with commentary + actions)
- Wire into orchestrator as pipeline Step 5 (after analysis)
- Persist report and recommendation records to SurrealDB
- Write tests with mocked LLM responses
- **Manual verification**: generate commentary for real portfolio data, assess quality

**Design Decisions:**
- **Google Gemini** — using `google-genai>=1.0` SDK with `gemini-2.5-flash` model (chosen over OpenAI/Anthropic for cost and structured output support)
- **Structured JSON output** — Gemini's `response_mime_type="application/json"` + `response_schema` ensures typed responses matching `CommentaryResponse` Pydantic model
- **Three-layer architecture** — `build_commentary_request()` (pure data assembly) → `format_prompt()` (pure rendering) → `generate_commentary()` (API call + parsing); layers 1–2 testable without API key
- **Graceful degradation** — if LLM API key is missing or call fails, pipeline continues with `commentary=None`
- **Orchestrator wiring** — Step 5 added to `run_data_pipeline()` after analysis; report + recommendations persisted via `db/reports.py`

**Acceptance Criteria:**
- [x] `generate_commentary(request, settings)` sends data to the configured LLM and returns structured output
- [x] LLM response is parsed into: summary headline, per-position commentary, recommended actions (with action type and conviction), and market context
- [x] Each recommendation includes an action (`buy`/`sell`/`hold`/`reduce`/`increase`), conviction (`high`/`medium`/`low`), and reasoning
- [x] Supports Google Gemini via the `LLM_PROVIDER` config setting (Gemini chosen over OpenAI/Anthropic)
- [x] Tests use mocked LLM responses and verify parsing logic
- [x] Manual test produces coherent, relevant commentary for the current portfolio
- [x] Commentary is wired into orchestrator as pipeline Step 5
- [x] Report and recommendation records are persisted to SurrealDB
- [x] Pipeline completes gracefully if LLM API key is missing or call fails
- [x] `scripts/run_pipeline.py` report includes LLM commentary section

### Step 10: Report Generation & Output
- Implement `reporting/generator.py`: assemble all data into a report structure
- Implement `reporting/formatter.py`: render as terminal output (rich) and markdown file
- Wire up full pipeline in `orchestrator.py` (all 6 steps)
- Write tests for report assembly and markdown formatting
- **Manual verification**: run full pipeline, review terminal output and saved markdown file

**Acceptance Criteria:**
- [x] `generate_report()` assembles portfolio snapshot, analyses, and LLM commentary into a `Report` object
- [x] Report is printed to terminal with readable formatting via `rich`
- [x] Report is saved as a timestamped markdown file in `reports/` (e.g. `2026-02-09_market_open.md`)
- [x] Report and recommendations are stored in SurrealDB
- [x] Full pipeline runs end-to-end: data fetch -> analysis -> LLM -> report output
- [x] Tests verify report assembly and markdown output structure

### Step 11: CLI & Run Logging
- Implement `main.py`: CLI with `--run-type` argument (market_open / market_close)
- Implement run logging: run_log table tracking status, duration, errors
- Add structured logging throughout with `structlog`
- Write tests for CLI argument parsing and run log lifecycle
- **Manual verification**: `python -m agent.main --run-type market_open` produces a complete report

**Acceptance Criteria:**
- [ ] `python -m agent.main --run-type market_open` runs the full pipeline and outputs a report
- [ ] `--run-type` accepts `market_open` and `market_close`; rejects invalid values
- [ ] Each run creates a `run_log` entry with: run_id, run_type, status, instruments_analysed, recommendations_made, duration_ms, errors
- [ ] Run log status transitions: `started` -> `completed` (or `failed` on error)
- [ ] `structlog` is configured and all components emit structured log entries
- [ ] Tests verify CLI argument parsing, run_log creation, and status transitions

### Step 12: Polish & Hardening
- Error handling: graceful degradation if eToro API is down or rate-limited
- Partial runs: if one instrument fails, continue with the rest and note the error
- Configuration: tracked instruments and LLM settings stored in SurrealDB `config` table
- Historical backfill: `scripts/backfill_candles.py` for seeding historical data
- Review and harden all tests

**Acceptance Criteria:**
- [ ] Agent exits gracefully (non-zero exit code, clear error message) if eToro API is unreachable
- [ ] If N instruments are tracked and 1 fails, the report covers the remaining N-1 and the run_log records the error
- [ ] `config` table stores tracked instrument list and LLM prompt settings; agent reads from it on startup
- [ ] `python scripts/backfill_candles.py` fetches and stores historical daily candles for all tracked instruments
- [ ] All existing tests still pass
- [ ] No unhandled exceptions in any error scenario (API down, DB down, LLM down, invalid data)

### Step 13: LangChain/LangGraph Agent Migration
- Replace `orchestrator.py` procedural pipeline with a LangGraph agent
- Wrap existing modules as LangGraph tools (see section 7.1)
- Add SurrealDB as agent memory (query previous runs, reports)
- The agent should produce the same report format as the procedural pipeline
- Write tests comparing agent output to expected report structure

**Acceptance Criteria:**
- [ ] LangGraph agent runs the same data pipeline as `Orchestrator.run_data_pipeline()`
- [ ] Agent uses tools to fetch portfolio, candles, and instrument metadata
- [ ] Agent can query SurrealDB for historical context (previous reports, analyses)
- [ ] Agent produces a report in the same format as the procedural pipeline
- [ ] Agent demonstrates at least one adaptive decision (e.g. fetching extra history for volatile instruments)
- [ ] All existing tests still pass — no regressions
- [ ] New tests verify agent tool invocations and report output

### Step 14: Telegram Bot Integration

Integrate a Telegram bot so the agent can push reports and alerts directly to the user's phone. The bot acts as a one-way notification channel in this step — interactive conversations come in Step 15.

**Architecture:**

```
src/agent/telegram/
  bot.py              — TelegramBot class: send messages, format reports for Telegram
  formatter.py        — Convert Report objects to Telegram-friendly markdown (MarkdownV2)

tests/telegram/
  test_bot.py         — Tests for TelegramBot message sending and error handling
  test_formatter.py   — Tests for Telegram markdown formatting
```

**Design Decisions:**
- **Direct Telegram Bot API via `httpx`** — use synchronous HTTP calls to the Bot API (no async, consistent with project design)
- **One-way push only** — this step sends reports to a configured chat ID; no incoming message handling yet
- **Graceful degradation** — if Telegram credentials are missing or the API fails, the pipeline completes normally (same pattern as LLM fallback in Step 9)
- **Formatted output** — reports are condensed into a Telegram-friendly format: headline, top recommendations, key P&L figures, with a link to the full markdown report
- **Pipeline wiring** — added as an optional sub-step within pipeline Step 6 (Output), after terminal and markdown output

**Setup:**
1. Create a bot via [BotFather](https://t.me/BotFather) and obtain the bot token
2. Get the target chat ID (personal chat or group)
3. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env`

**Acceptance Criteria:**
- [ ] `TelegramBot` class sends messages to a configured chat ID using the Bot API
- [ ] `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are loaded from `.env` via `pydantic-settings`
- [ ] Report summary is formatted for Telegram: headline, portfolio P&L, top 3 recommendations, market context snippet
- [ ] Messages respect Telegram's 4096-character limit — long reports are split into multiple messages
- [ ] Pipeline Step 6 sends the report via Telegram after terminal and markdown output
- [ ] If Telegram credentials are missing, the pipeline completes without error (warning logged)
- [ ] If the Telegram API call fails, the error is logged but the pipeline does not abort
- [ ] Tests mock the Telegram API and verify message formatting and send logic
- [ ] Manual test confirms a real message is delivered to the configured Telegram chat

### Step 15: Fully Agentic Conversational Platform

Transform the agent from a batch-run tool into a **fully interactive, conversational assistant** accessible via Telegram. Users can text the bot with natural language questions (e.g. "How is my portfolio doing?", "What's the trend on AAPL?", "Should I reduce my crypto exposure?") and the agent responds intelligently using its full toolkit.

This step builds on the LangGraph migration (Step 13) and the Telegram bot (Step 14) to create an always-on assistant that combines real-time data access, historical context, and LLM reasoning.

**Architecture:**

```
src/agent/telegram/
  bot.py              — Extended: add incoming message handler, conversation management
  handlers.py         — Route incoming messages to the agent, manage conversation state
src/agent/skills/
  __init__.py          — Skill registry
  portfolio.py         — Skill: fetch and summarise current portfolio
  instrument.py        — Skill: look up instrument details, current price, trend
  analysis.py          — Skill: run on-demand analysis for a specific instrument
  history.py           — Skill: query historical reports, compare performance over time
  report.py            — Skill: generate a full or partial report on demand
  watchlist.py         — Skill: manage and query watchlist instruments
src/agent/mcp/
  server.py            — MCP (Model Context Protocol) server exposing skills as tools
  protocol.py          — MCP request/response types and tool definitions
src/agent/conversational.py — Conversational agent: receives user message, selects skills, generates response

tests/skills/          — Skill unit tests (mocked dependencies)
tests/mcp/             — MCP server and protocol tests
tests/telegram/        — Extended Telegram handler and conversation tests
```

**Design Decisions:**
- **MCP (Model Context Protocol)** — skills are exposed as MCP tools with typed input/output schemas, making them discoverable and composable by the LLM agent. This follows the emerging standard for LLM-tool interoperability
- **Skill-based architecture** — each skill is a self-contained unit that wraps existing modules (eToro client, DB queries, analysis engine). Skills declare their name, description, input schema, and execute method
- **Conversational agent** — a LangGraph `ReAct` agent that receives user messages, reasons about which skills to invoke, calls them, and synthesises a natural language response
- **Telegram as the interface (separate runtime)** — this step intentionally introduces a **separate long-polling process** for the conversational bot, distinct from the core agent's single-invocation CLI model. The scheduled pipeline (Steps 1–12) continues to run as a batch CLI command on a cron schedule, while the conversational bot runs as a standalone long-lived service. Deployment considerations (process manager, systemd, Docker container) are addressed in this step. Each user gets a conversation thread with context retention
- **Context window** — the agent has access to recent conversation history (last N messages) plus SurrealDB long-term memory (previous reports, analyses, portfolio history) for informed responses
- **Permission model** — read-only; the agent can fetch data and run analyses but cannot execute trades (consistent with the project's advisory-only principle)

**MCP Tool Definitions:**

| Tool | Description | Input | Output |
|---|---|---|---|
| `get_portfolio_summary` | Current portfolio overview with P&L | None | Portfolio positions, total value, daily P&L |
| `get_instrument_price` | Current price and basic info for an instrument | `{ symbol: string }` | Bid, ask, daily change, instrument metadata |
| `analyse_instrument` | Run full technical analysis on an instrument | `{ symbol: string, timeframe?: string }` | Trend, momentum, support/resistance, signal |
| `compare_performance` | Compare instrument/portfolio performance over time | `{ symbol?: string, period: string }` | Performance metrics, comparison data |
| `get_latest_report` | Fetch the most recent generated report | `{ run_type?: string }` | Full report with commentary and recommendations |
| `search_instruments` | Search for instruments by name or symbol | `{ query: string }` | List of matching instruments |
| `get_market_overview` | Sector-level market summary | None | Per-sector average returns, notable movers |

**Conversation Flow:**

```
User: "How is Tesla doing?"
  │
  ▼
Conversational Agent (LangGraph ReAct)
  │
  ├─ Reason: User wants Tesla analysis
  ├─ Call skill: get_instrument_price({ symbol: "TSLA" })
  ├─ Call skill: analyse_instrument({ symbol: "TSLA" })
  ├─ Synthesise: Combine price data + analysis into natural language
  │
  ▼
Bot: "TSLA is currently at $248.50 (+1.2% today). The trend is
      bullish with strong momentum — it's been making higher highs
      over the past 2 weeks. Key support at $240, resistance at $255.
      Your position is up 8.3% overall."
```

**Acceptance Criteria:**
- [ ] Telegram bot handles incoming messages and routes them to the conversational agent
- [ ] At least 6 MCP skills are implemented: portfolio summary, instrument price, instrument analysis, performance comparison, latest report, instrument search
- [ ] Each skill has a typed input/output schema and is registered in the MCP server
- [ ] Conversational agent uses LangGraph ReAct pattern to reason about which skills to invoke
- [ ] Agent can answer portfolio questions ("How is my portfolio?", "What's my best performer?")
- [ ] Agent can answer instrument questions ("What's the trend on AAPL?", "Is Bitcoin bullish?")
- [ ] Agent can generate on-demand reports ("Give me a market update", "Analyse my crypto positions")
- [ ] Conversation context is maintained within a session (agent remembers earlier messages in the thread)
- [ ] SurrealDB historical data is accessible as long-term memory (previous reports, analyses)
- [ ] Agent responds within a reasonable time (~5-15 seconds for most queries)
- [ ] Unknown or out-of-scope queries are handled gracefully ("I can help with portfolio and market questions...")
- [ ] All skills have unit tests with mocked dependencies
- [ ] Integration tests verify the full message → agent → skill → response flow
- [ ] Manual test demonstrates a multi-turn conversation via Telegram with real data

### Future Phases (Post-MVP)

| Phase | Description |
|---|---|
| **Automated Trading** | Add write-permission API key, implement position open/close, risk manager |
| **AWS Deployment** | Containerise with Docker, deploy to ECS/Fargate, trigger via EventBridge schedule |
| **Dashboard** | Simple web UI to browse historical reports (SurrealDB live queries) |
| **Strategy Backtesting** | Test analysis rules against historical candle data |
| **Voice Interface** | Add Telegram voice message support — transcribe queries and respond with voice notes |
| **Multi-User Support** | Per-user API key management, isolated portfolio tracking, role-based access |

### 7.1 LangChain/LangGraph Migration (Step 13 Detail)

> **Note:** This section provides additional design context for **Step 13** in the roadmap above. It is not a separate future phase — Step 13 is an in-roadmap step to be completed after Step 12.

The current `Orchestrator` is a procedural pipeline — a fixed sequence of steps with no decision-making. Step 13 replaces this with an agentic architecture where an LLM decides what to fetch, analyse, and report on.

**Current state:** Fixed pipeline: fetch portfolio → resolve instruments → fetch candles → analyse → LLM → report. Every instrument gets the same treatment regardless of context.

**Target state:** A LangGraph agent with tools that can make adaptive decisions:
- Fetch more history if a trend is unclear
- Skip stable positions that haven't moved
- Deep-dive on volatile instruments with additional timeframes
- Adjust analysis depth based on portfolio risk

**Tools to expose as LangGraph tools:**

| Tool | Maps to | Purpose |
|---|---|---|
| `fetch_portfolio` | `etoro.portfolio.get_portfolio()` | Get current positions |
| `fetch_candles` | `etoro.market_data.get_candles()` | Get OHLCV history |
| `search_instrument` | `etoro.market_data.search_instruments()` | Resolve ticker symbols |
| `query_db` | `db.*` query functions | Read historical data, previous reports |
| `analyse_price_action` | `analysis.price_action.analyse()` | Run technical analysis |
| `generate_report` | `reporting.generator.generate()` | Assemble final report |

**Memory:** SurrealDB serves as long-term memory — previous runs, reports, analyses, and portfolio history are already persisted. The agent can query this context to inform decisions.

**Key design constraint:** Tools must be thin wrappers around existing modules. The Step 13 migration should reuse all Step 2–12 code, only replacing the orchestration layer. This means the MVP's modular architecture (separate etoro/, db/, analysis/, reporting/ packages) is preserved.

**Dependencies to add:** `langchain`, `langgraph`, `langchain-openai` / `langchain-anthropic`

---

## 8. Local Development Setup

### Prerequisites
- Python 3.11+
- Docker Desktop (for SurrealDB)
- eToro account with API keys (Read permission)

### Setup
```bash
# Clone / enter project directory
cd etoro-trading-agent

# Start SurrealDB
docker compose up -d

# Install the project in development mode
pip install -e ".[dev]"

# Copy and fill in environment variables
cp .env.example .env
# Edit .env: add eToro API key, user key, LLM API key

# Initialise the database schema
python scripts/init_db.py

# Run the agent
python -m agent.main --run-type market_open

# Run tests
pytest
```

### docker-compose.yml

```yaml
services:
  surrealdb:
    image: surrealdb/surrealdb:latest
    command: start --user root --pass root file:/data/trading.db
    ports:
      - "8000:8000"
    volumes:
      - surrealdb_data:/data

volumes:
  surrealdb_data:
```

### Environment Variables (.env.example)

```env
# eToro API
ETORO_API_KEY=your-api-key-here
ETORO_USER_KEY=your-user-key-here
ETORO_BASE_URL=https://public-api.etoro.com/api/v1

# SurrealDB
SURREAL_URL=ws://localhost:8000/rpc
SURREAL_NAMESPACE=trading
SURREAL_DATABASE=agent
SURREAL_USER=root
SURREAL_PASS=root

# LLM (for market commentary)
LLM_PROVIDER=openai          # or anthropic
LLM_API_KEY=your-llm-key
LLM_MODEL=gpt-4o             # or claude-sonnet-4-20250514

# Telegram Bot (optional — report delivery and conversational agent)
TELEGRAM_BOT_TOKEN=your-bot-token-from-botfather
TELEGRAM_CHAT_ID=your-chat-id
```

---

## 9. AWS Future Architecture (Reference Only)

Not part of MVP, but the project structure supports this migration:

```
EventBridge (cron: 08:00, 16:30 GMT)
    │
    ▼
ECS Fargate Task (runs agent container)
    │
    ├──► eToro API
    ├──► SurrealDB (on EC2 or SurrealDB Cloud)
    ├──► LLM API (OpenAI / Anthropic)
    └──► S3 (store report markdown files)
         │
         ▼
    SNS / SES (email report summary)
```

---

## 10. Work Tracker

Each row maps to a discrete PR. Complete and merge each PR before starting the next. Update this table as work progresses.

| PR | Title | Roadmap Steps | Key Deliverables | Status |
|---|---|---|---|---|
| #1 | Project scaffolding | Step 1 | `pyproject.toml`, directory structure, `.env.example`, `docker-compose.yml`, pytest config | Done |
| #4 | eToro API client - auth | Step 2 | `etoro/client.py`, `config.py`, auth header tests, error handling tests | Done |
| #5 | eToro API client - market data | Step 3 | `etoro/market_data.py`, `etoro/models.py`, mocked endpoint tests | Done |
| #6 | eToro API client - portfolio | Step 4 | `etoro/portfolio.py`, portfolio response models, mocked tests | Done |
| #7 | SurrealDB connection & schema | Step 5 | `db/connection.py`, `db/schema.py`, `scripts/init_db.py`, schema tests | Done |
| #8 | SurrealDB data layer | Step 6 | `db/utils.py`, `db/instruments.py`, `db/candles.py`, `db/snapshots.py`, `db/reports.py`, CRUD tests | Done |
| TBD | End-to-end data pipeline | Step 7 | `orchestrator.py` (data fetch + store), `scripts/run_pipeline.py`, integration tests | Done |
| TBD | Analysis engine | Step 8 | `analysis/types.py`, `analysis/registry.py`, `analysis/indicators/`, `analysis/price_action.py`, `analysis/sector.py`, `db/analysis.py`, orchestrator Step 4, analysis tests | Done |
| TBD | LLM commentary | Step 9 | `reporting/llm.py`, prompt design, structured output parsing, orchestrator Step 5 wiring, mocked tests | Done |
| TBD | Report generation & output | Step 10 | `reporting/generator.py`, `reporting/formatter.py`, full pipeline wiring, report tests | Done |
| TBD | CLI & run logging | Step 11 | `main.py` CLI, `run_log` lifecycle, structured logging, CLI tests | Done |
| TBD | Polish & hardening | Step 12 | Error handling, partial runs, config table, `backfill_candles.py`, test review | Done |
| TBD | LangChain/LangGraph agent migration | Step 13 | LangGraph agent, tool wrappers, SurrealDB memory, agent tests | Not Started |
| TBD | Telegram bot integration | Step 14 | `telegram/bot.py`, `telegram/formatter.py`, report push notifications, Telegram tests | Not Started |
| TBD | Fully agentic conversational platform | Step 15 | `agent/skills/`, `agent/mcp/`, `conversational.py`, Telegram message handling, MCP server, skill tests | Not Started |

**Status values:** `Not Started` | `In Progress` | `In Review` | `Done`

---

## 11. Open Questions

| Question | Current Assumption |
|---|---|
| **Which LLM for commentary?** | OpenAI GPT-4o or Anthropic Claude - both supported, configurable |
| **eToro API rate limits?** | Unknown - will discover in Step 2 and implement accordingly |
| **Which instruments to track initially?** | Will configure a starter set of ~15-20 across all asset classes |
| **Report retention policy?** | Keep all reports in SurrealDB indefinitely, markdown files in reports/ |
| **Telegram bot hosting?** | Long-polling for MVP (no webhook server needed); move to webhook on AWS deployment |
| **Conversation rate limits?** | Unknown — will need to handle Telegram API rate limits and LLM costs for interactive queries |
| **Multi-user support for Telegram?** | Single-user only for MVP; restrict bot to one configured chat ID |
| **MCP vs native LangGraph tools?** | Start with MCP for interoperability; evaluate if native LangGraph tools are simpler for the MVP |
