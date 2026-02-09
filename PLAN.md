# eToro Trading Agent - Implementation Plan

## Overview

A Python-based **advisory trading agent** that runs twice daily at UK market open (08:00 GMT) and close (16:30 GMT). The agent fetches market data and portfolio state from eToro's public API, analyses price action and sector context, uses an LLM to generate natural language commentary, and produces a **report of recommended actions** - it does not execute trades automatically.

SurrealDB is used as the persistence layer, storing market data, portfolio snapshots, reports, and configuration. The system runs locally for now but is structured to be deployable to AWS in the future.

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
DEFINE FIELD price_action    ON analysis TYPE object;           -- key price levels, patterns
DEFINE FIELD sector_context  ON analysis TYPE option<object>;   -- sector performance, rotation
DEFINE FIELD raw_data        ON analysis TYPE object;           -- snapshot of input data used
DEFINE FIELD created_at      ON analysis TYPE datetime          DEFAULT time::now();

-- ============================================================
-- REPORTS (the final output)
-- ============================================================
DEFINE TABLE report SCHEMAFULL;
DEFINE FIELD run_id          ON report TYPE string;
DEFINE FIELD run_type        ON report TYPE string;             -- market_open, market_close
DEFINE FIELD portfolio_snapshot ON report TYPE record<portfolio_snapshot>;
DEFINE FIELD recommendations ON report TYPE array;              -- array of action recommendations
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
- [ ] `pyproject.toml` exists with all dependencies from section 5, including `[dev]` extras
- [ ] Full directory structure under `src/agent/` matches section 4 (all `__init__.py` files present)
- [ ] `.env.example` contains all variables from section 8 with placeholder values
- [ ] `docker-compose.yml` starts SurrealDB successfully with `docker compose up -d`
- [ ] `pip install -e ".[dev]"` completes without errors
- [ ] `pytest` runs and exits 0 (no tests collected is acceptable)
- [ ] `reports/` directory exists with `.gitkeep`, and is gitignored
- [ ] No secrets or real API keys are committed

### Step 2: eToro API Client - Authentication
- Implement `etoro/client.py`: base HTTP client with auth headers
- Implement `config.py`: load API keys from `.env`
- Write tests: verify auth headers are set correctly, request IDs are unique UUIDs
- Write tests: verify error handling for 401/403 responses
- **Manual verification**: make a single authenticated request to eToro API and confirm 200 response

**Acceptance Criteria:**
- [ ] `EToroClient` class sends `x-api-key`, `x-user-key`, and a unique `x-request-id` UUID on every request
- [ ] Config loads all eToro/SurrealDB/LLM settings from `.env` via `pydantic-settings`
- [ ] Client retries failed requests up to 3 times with exponential backoff
- [ ] 401 and 403 responses raise clear, descriptive exceptions
- [ ] All tests pass with `pytest` - no real API calls in tests
- [ ] Manual test confirms a 200 response from at least one eToro endpoint

### Step 3: eToro API Client - Market Data
- Implement `etoro/market_data.py`: instrument search, OHLCV fetch, price fetch
- Implement `etoro/models.py`: Pydantic models for API responses
- Write tests with mocked HTTP responses for each endpoint
- **Manual verification**: fetch real OHLCV data for 2-3 instruments, inspect output

**Acceptance Criteria:**
- [ ] `search_instruments(query)` returns a list of instruments with `etoro_id`, `symbol`, `name`, `asset_class`
- [ ] `get_candles(instrument_id, timeframe)` returns OHLCV data parsed into Pydantic models
- [ ] `get_prices(instrument_ids)` returns current bid/ask for each instrument
- [ ] All API responses are validated through Pydantic models (invalid data raises, not silently ignored)
- [ ] Each endpoint has at least one test with mocked HTTP responses
- [ ] Manual test fetches real candle data for a stock, a crypto, and an ETF

### Step 4: eToro API Client - Portfolio
- Implement `etoro/portfolio.py`: portfolio positions, trading history
- Add Pydantic models for portfolio responses
- Write tests with mocked portfolio data
- **Manual verification**: fetch real portfolio state, confirm positions match eToro UI

**Acceptance Criteria:**
- [ ] `get_portfolio()` returns current positions with instrument, amount, P&L, open date
- [ ] `get_trading_history()` returns closed trades with entry/exit prices and P&L
- [ ] Portfolio and history responses are validated through Pydantic models
- [ ] Each endpoint has at least one test with mocked HTTP responses
- [ ] Manual test confirms position count and instruments match what is shown in the eToro UI

### Step 5: SurrealDB Connection & Schema
- Implement `db/connection.py`: connect to SurrealDB, handle lifecycle
- Implement `db/schema.py`: apply the SurrealQL schema
- Write `scripts/init_db.py`: CLI script to initialise a fresh database
- Write tests: schema applies cleanly, tables exist, indexes are created
- **Manual verification**: `docker compose up -d`, run init script, query tables via SurrealDB CLI

**Acceptance Criteria:**
- [ ] `get_connection()` connects to SurrealDB using env vars and selects the correct namespace/database
- [ ] `apply_schema()` executes all SurrealQL from section 3 without errors
- [ ] `python scripts/init_db.py` applies schema to a running SurrealDB instance
- [ ] Running `init_db.py` twice is idempotent (no errors on re-apply)
- [ ] Tests verify all 8 tables exist (`instrument`, `candle`, `portfolio_snapshot`, `analysis`, `report`, `recommendation`, `run_log`, `config`)
- [ ] Tests verify indexes are created (`idx_symbol`, `idx_etoro_id`, `idx_candle_lookup`, `idx_run_id`, `idx_config_key`)

### Step 6: SurrealDB Data Layer
- Implement `db/instruments.py`: upsert and query instruments
- Implement `db/candles.py`: store and query OHLCV candles
- Implement `db/snapshots.py`: store and query portfolio snapshots
- Implement `db/reports.py`: store and query reports
- Write tests for each module (insert, query, upsert, edge cases)

**Acceptance Criteria:**
- [ ] `instruments.py`: upsert by symbol (insert or update), query by symbol, query by etoro_id, list all
- [ ] `candles.py`: bulk insert candles, query by instrument + timeframe + date range, no duplicate insertion
- [ ] `snapshots.py`: create snapshot, query latest, query by date range
- [ ] `reports.py`: create report with recommendations, query by run_id, query latest, list by date range
- [ ] Each module has tests covering insert, query, upsert, and edge cases (empty results, duplicates)
- [ ] All tests pass against a real SurrealDB test instance (not mocked)

### Step 7: End-to-End Data Pipeline
- Implement `orchestrator.py` (steps 1-3 of the run pipeline only)
- Wire up: fetch instruments from eToro -> store in SurrealDB -> fetch candles -> store -> fetch portfolio -> snapshot
- Write integration test: mock eToro API, verify data flows through to SurrealDB
- **Manual verification**: run pipeline, query SurrealDB to confirm data is stored correctly

**Acceptance Criteria:**
- [ ] `Orchestrator.run_data_pipeline()` executes steps 1-3 of the run pipeline (init, fetch market data, fetch portfolio)
- [ ] Instruments are resolved via eToro API and stored/updated in SurrealDB
- [ ] OHLCV candles are fetched for all tracked instruments and stored in SurrealDB
- [ ] Portfolio is fetched and a snapshot is saved to SurrealDB
- [ ] Integration test with mocked eToro API confirms data flows end-to-end
- [ ] A failed instrument fetch does not abort the entire pipeline

### Step 8: Analysis Engine
- Implement `analysis/price_action.py`: trend detection (higher highs/lows, moving average direction), momentum (rate of change), key price levels
- Implement `analysis/sector.py`: group instruments by sector/asset class, compare relative performance
- Write tests with known price data and expected analysis results
- **Manual verification**: analyse a few instruments, manually verify trend assessments make sense

**Acceptance Criteria:**
- [ ] `analyse_price_action(candles)` returns trend direction (bullish/bearish/neutral), trend strength (0-1), key support/resistance levels, and momentum assessment
- [ ] `analyse_sector(instruments, candles)` groups instruments by asset class/sector and compares relative performance
- [ ] Analysis functions are pure (take data in, return results) with no API or DB calls
- [ ] Tests use fixed candle data with known trends and verify correct classification
- [ ] Manual test on 3+ real instruments produces sensible assessments

### Step 9: LLM Commentary
- Implement `reporting/llm.py`: send structured analysis data to LLM, receive natural language commentary
- Design the LLM prompt: portfolio state + analysis data -> market commentary + recommendations
- Implement structured output parsing (LLM returns JSON with commentary + actions)
- Write tests with mocked LLM responses
- **Manual verification**: generate commentary for real portfolio data, assess quality

**Acceptance Criteria:**
- [ ] `generate_commentary(portfolio, analyses)` sends data to the configured LLM and returns structured output
- [ ] LLM response is parsed into: summary headline, per-position commentary, recommended actions (with action type and conviction), and market context
- [ ] Each recommendation includes an action (`buy`/`sell`/`hold`/`reduce`/`increase`), conviction (`high`/`medium`/`low`), and reasoning
- [ ] Supports both OpenAI and Anthropic via the `LLM_PROVIDER` config setting
- [ ] Tests use mocked LLM responses and verify parsing logic
- [ ] Manual test produces coherent, relevant commentary for the current portfolio

### Step 10: Report Generation & Output
- Implement `reporting/generator.py`: assemble all data into a report structure
- Implement `reporting/formatter.py`: render as terminal output (rich) and markdown file
- Wire up full pipeline in `orchestrator.py` (all 6 steps)
- Write tests for report assembly and markdown formatting
- **Manual verification**: run full pipeline, review terminal output and saved markdown file

**Acceptance Criteria:**
- [ ] `generate_report()` assembles portfolio snapshot, analyses, and LLM commentary into a `Report` object
- [ ] Report is printed to terminal with readable formatting via `rich`
- [ ] Report is saved as a timestamped markdown file in `reports/` (e.g. `2026-02-09_market_open.md`)
- [ ] Report and recommendations are stored in SurrealDB
- [ ] Full pipeline runs end-to-end: data fetch -> analysis -> LLM -> report output
- [ ] Tests verify report assembly and markdown output structure

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

### Future Phases (Post-MVP)

| Phase | Description |
|---|---|
| **Automated Trading** | Add write-permission API key, implement position open/close, risk manager |
| **AWS Deployment** | Containerise with Docker, deploy to ECS/Fargate, trigger via EventBridge schedule |
| **Notifications** | Send report summary via Telegram/Discord/email |
| **Dashboard** | Simple web UI to browse historical reports (SurrealDB live queries) |
| **Strategy Backtesting** | Test analysis rules against historical candle data |

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
| #2 | eToro API client - auth | Step 2 | `etoro/client.py`, `config.py`, auth header tests, error handling tests | In Review |
| #3 | eToro API client - market data | Step 3 | `etoro/market_data.py`, `etoro/models.py`, mocked endpoint tests | Not Started |
| #4 | eToro API client - portfolio | Step 4 | `etoro/portfolio.py`, portfolio response models, mocked tests | Not Started |
| #5 | SurrealDB connection & schema | Step 5 | `db/connection.py`, `db/schema.py`, `scripts/init_db.py`, schema tests | Not Started |
| #6 | SurrealDB data layer | Step 6 | `db/instruments.py`, `db/candles.py`, `db/snapshots.py`, `db/reports.py`, CRUD tests | Not Started |
| #7 | End-to-end data pipeline | Step 7 | `orchestrator.py` (data fetch + store), integration test with mocked API | Not Started |
| #8 | Analysis engine | Step 8 | `analysis/price_action.py`, `analysis/sector.py`, analysis tests | Not Started |
| #9 | LLM commentary | Step 9 | `reporting/llm.py`, prompt design, structured output parsing, mocked tests | Not Started |
| #10 | Report generation & output | Step 10 | `reporting/generator.py`, `reporting/formatter.py`, full pipeline wiring, report tests | Not Started |
| #11 | CLI & run logging | Step 11 | `main.py` CLI, `run_log` lifecycle, structured logging, CLI tests | Not Started |
| #12 | Polish & hardening | Step 12 | Error handling, partial runs, config table, `backfill_candles.py`, test review | Not Started |
| #13 | CI workflow for pytest | N/A | `.github/workflows/pytest.yml` | In Review |

**Status values:** `Not Started` | `In Progress` | `In Review` | `Done`

---

## 11. Open Questions

| Question | Current Assumption |
|---|---|
| **Which LLM for commentary?** | OpenAI GPT-4o or Anthropic Claude - both supported, configurable |
| **eToro API rate limits?** | Unknown - will discover in Step 2 and implement accordingly |
| **Which instruments to track initially?** | Will configure a starter set of ~15-20 across all asset classes |
| **Report retention policy?** | Keep all reports in SurrealDB indefinitely, markdown files in reports/ |
