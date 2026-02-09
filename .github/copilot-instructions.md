# Copilot Instructions for eToro Trading Assistant

## Project Overview

This is a **Python-based advisory trading agent** that integrates with eToro's public API and uses SurrealDB for persistence. The agent runs twice daily (UK market open and close), analyses market data and portfolio positions, and generates a report with recommended actions and market commentary. It does **not** execute trades automatically - it is read-only.

**Key characteristics:**
- Language: Python 3.11+
- Type: CLI application (not a daemon or web service)
- Size: Small/MVP stage project
- External dependencies: eToro API, SurrealDB, LLM API (OpenAI/Anthropic)
- Design philosophy: Simple, synchronous, single-invocation execution

## Repository Structure

```
src/agent/          - All application source code
  etoro/            - eToro API client layer
  db/               - SurrealDB data access layer
  analysis/         - Price action and sector analysis
  reporting/        - Report generation, LLM commentary, formatting
  utils/            - Shared utilities (logging)
tests/              - All test files (mirrors src structure)
scripts/            - One-off utility scripts (currently empty)
reports/            - Generated report output (gitignored)
```

**Configuration files:**
- `pyproject.toml` - Python package configuration, dependencies, pytest settings
- `.env` - Secrets and connection details (never committed, use `.env.example` as template)
- `docker-compose.yml` - SurrealDB service configuration
- `PLAN.md` - Full implementation plan, architecture, schema, roadmap
- `AGENTS.md` - Detailed AI agent instructions (see this file for full context)

## Environment Setup

**Required:**
- Python 3.11 or higher (tested with Python 3.12.3)
- Virtual environment must be created and activated before any operations

**Setup steps (must be run in this order):**

```bash
# 1. Create virtual environment (if not exists)
python -m venv .venv

# 2. Activate virtual environment
# On Unix/macOS:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# 3. Install dependencies (always do this after creating venv)
pip install -e .

# 4. Install development dependencies
pip install -e ".[dev]"

# 5. Copy environment template (first time only)
cp .env.example .env
# Then edit .env with actual credentials

# 6. Start SurrealDB (required for tests)
docker-compose up -d
```

**Important:** Always run Python commands from within the activated virtual environment. If the venv is not activated, use explicit paths like `.venv/Scripts/python.exe`, `.venv/Scripts/pip.exe`, and `.venv/Scripts/pytest.exe`.

## Build & Test

**There is no build step** - Python is interpreted. The package is installed in development mode with `pip install -e .`

**Running tests:**

```bash
# Run all tests (takes ~2-3 seconds)
pytest

# Run specific test file
pytest tests/test_smoke.py

# Run with verbose output
pytest -v

# Run with coverage (if pytest-cov is installed)
pytest --cov=agent
```

**Test requirements:**
- SurrealDB must be running (`docker-compose up -d`)
- Tests use mocking for external APIs (eToro, LLM) - never make real API calls
- All new functionality must have tests - this is non-negotiable
- Test files must be named `test_*.py` and placed in `tests/` directory

**Current test status:**
- Basic smoke test exists (`test_smoke.py`) that verifies package imports
- Test infrastructure is minimal - more tests will be added as features are implemented

## Code Style & Conventions

**Python standards:**
- Follow PEP 8 conventions
- Use snake_case for functions/variables, PascalCase for classes
- All function signatures must have type hints for parameters and return values
- No async/await - everything is synchronous
- File names use snake_case.py (no abbreviations)

**Testing standards:**
- Test files named `test_<module>.py`
- Use descriptive test names: `test_client_sets_auth_headers`, not `test_client_1`
- Mock all external API calls
- Tests must be deterministic and not depend on external services

**Commit conventions:**
- Use conventional commit style: `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`
- Keep commits focused - one logical change per commit
- Reference roadmap steps where relevant (e.g. `feat: implement eToro auth client (step 2)`)

## Key Dependencies

| Package | Purpose | Notes |
|---------|---------|-------|
| httpx | HTTP client for eToro API | Synchronous client only |
| surrealdb | SurrealDB Python SDK | For data persistence |
| pydantic | Data validation and models | Use for all API responses |
| pydantic-settings | Environment variable loading | For config management |
| pandas | Data manipulation for analysis | For price/sector analysis |
| structlog | Structured logging | JSON output format |
| openai/anthropic | LLM API for market commentary | Choose one based on env |
| pytest | Testing framework | - |
| pytest-httpx | HTTP mocking in tests | Mock eToro API calls |
| rich | Terminal output formatting | For report display |

**Dependency management:**
- Do not add packages beyond this list without explicit approval
- Prefer standard library or existing dependencies over adding new ones
- All dependencies declared in `pyproject.toml`

## What NOT To Do

**Architecture constraints:**
- Do not add WebSocket or real-time streaming code
- Do not add trade execution capability (open/close positions)
- Do not add async/await patterns
- Do not add a web UI or dashboard
- Do not add notification systems (email, Telegram, etc.)
- Do not add long-running daemon/server code

**Code quality constraints:**
- Do not skip writing tests for any new functionality
- Do not hardcode API keys or secrets
- Do not modify the SurrealDB schema without updating `PLAN.md` section 3
- Do not create new top-level directories without explicit instruction
- Do not create documentation files beyond what exists (PLAN.md, AGENTS.md, README.md)

**Development constraints:**
- Do not jump ahead in the roadmap (see `PLAN.md` section 7)
- Do not over-engineer solutions - this is an MVP
- Do not add features beyond the current roadmap step
- Do not reorganize project structure without explicit instruction

## Error Handling Guidelines

- If the eToro API is unavailable, log the error and exit gracefully - do not crash
- If a single instrument fails, skip it and continue with the rest
- All errors must be logged with sufficient context to debug
- Never silently swallow exceptions
- Use retry with exponential backoff (3 attempts) for transient API failures

## eToro API Specifics

**Required headers for all requests:**
- `x-request-id` - UUID v4 (generate new for each request)
- `x-api-key` - From environment variable
- `x-user-key` - From environment variable

**Base URL:** Set via `ETORO_BASE_URL` environment variable (default: https://public-api.etoro.com/api/v1)

**Permissions:** Only use Read permission endpoints - no trading/write operations allowed

## SurrealDB Specifics

**Connection details from environment:**
- `SURREAL_URL` - WebSocket URL (default: ws://localhost:8000/rpc)
- `SURREAL_NAMESPACE` - Database namespace (default: trading)
- `SURREAL_DATABASE` - Database name (default: agent)
- `SURREAL_USER` and `SURREAL_PASS` - Credentials

**Schema:**
- All tables use `SCHEMAFULL` - no schemaless tables
- Schema defined in `PLAN.md` section 3
- Use `record<table>` types for foreign key references
- Schema applied via `db/schema.py`

## Current Development Status

This project is in early MVP stage. See `PLAN.md` section 10 (Work Tracker) for current status of implementation steps. Follow the roadmap sequentially - do not skip ahead or implement later steps before earlier ones are complete.

## Additional Context

For comprehensive coding guidelines, architectural decisions, and detailed roadmap, always refer to `AGENTS.md` and `PLAN.md`. These files contain the authoritative guidance for this repository.
