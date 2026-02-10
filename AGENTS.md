# AGENTS.md

Instructions for AI coding agents working on this repository.

## Project Overview

This is a **Python-based advisory trading agent** that integrates with eToro's public API and uses SurrealDB for persistence. The agent runs twice daily (UK market open and close), analyses market data and portfolio positions, and generates a report with recommended actions and market commentary. It does **not** execute trades automatically.

See `PLAN.md` for the full implementation plan, architecture, schema, and roadmap.

## Core Principles

### 1. This is an MVP

Keep things simple. Do not over-engineer solutions or add features beyond what is specified in the current roadmap step. Avoid premature abstractions - write straightforward code that does one thing well.

### 2. No Automated Trading

The agent is **read-only** against the eToro API. It fetches market data and portfolio state but never opens, closes, or modifies positions. Any code that would execute trades is out of scope. The only output is a report (terminal + markdown file).

### 3. No Real-Time Streaming

The agent runs as a single CLI invocation, not a long-running daemon. There are no WebSocket connections, no live queries, no background processes. Each run fetches data, analyses it, produces a report, and exits.

### 4. Incremental Development

Follow the roadmap steps in `PLAN.md` section 7. Each step should be completed and tested before moving on. Do not jump ahead or start later steps before earlier ones are verified.

## Technical Guidelines

### Python

- **Python 3.11+** is the minimum version
- Use **type hints** throughout - all function signatures must have parameter and return type annotations
- Use `httpx` for HTTP requests (synchronous client, not async)
- Use `pydantic` for data validation and settings management
- Use `structlog` for logging (structured JSON output)
- Follow standard Python conventions (PEP 8, snake_case for functions/variables, PascalCase for classes)
- Always run Python, pip, and pytest from the local `.venv` for this repo. If the venv is not activated, use explicit paths like `.venv\\Scripts\\python.exe`, `.venv\\Scripts\\pip.exe`, and `.venv\\Scripts\\pytest.exe`.

### Testing

- **Every piece of functionality must have tests** - this is non-negotiable
- Use `pytest` as the testing framework
- External API calls (eToro, LLM) must be mocked in tests - never make real API calls in tests
- SurrealDB tests should use a test database instance (separate namespace or in-memory)
- Test files go in `tests/` and mirror the source structure
- Use descriptive test names: `test_client_sets_auth_headers`, not `test_client_1`
- Run `pytest` after any code change to verify nothing is broken

### eToro API

- All requests must include the three required headers: `x-request-id` (UUID v4), `x-api-key`, `x-user-key`
- Implement retry with exponential backoff (3 attempts) for transient failures
- Never hardcode API keys - always load from environment variables
- Use Pydantic models to validate API responses
- Only use **Read** permission endpoints - no trading/write operations

### SurrealDB

- Schema is defined in `PLAN.md` section 3 and applied via `db/schema.py`
- Use the official `surrealdb` Python SDK
- All tables use `SCHEMAFULL` - no schemaless tables
- Use `record<table>` types for foreign key references between tables
- Connection details come from environment variables, never hardcoded
- **AWS migration:** The DB connection layer must work with remote (`ws://`), in-memory (`memory`), and embedded file-based (`file://`) URLs via the same `SURREAL_URL` setting. This allows the agent to run on AWS as a standalone CLI process without a separate DB server. Do not introduce Docker-only assumptions in production code paths.

### Configuration

- Secrets and connection details go in `.env` (never committed to git)
- `.env.example` contains the template with placeholder values
- Use `pydantic-settings` to load and validate environment variables
- Runtime configuration (tracked instruments, LLM prompts) is stored in the SurrealDB `config` table

### Error Handling

- If the eToro API is unavailable, log the error and exit gracefully - do not crash
- If a single instrument fails, skip it and continue with the rest, noting the failure in the run log
- All errors must be logged with sufficient context to debug (request details, response status, etc.)
- Never silently swallow exceptions

### Project Structure

```
src/agent/          - All application source code
  etoro/            - eToro API client layer
  db/               - SurrealDB data access layer
  analysis/         - Price action and sector analysis
  reporting/        - Report generation, LLM commentary, formatting
  utils/            - Shared utilities (logging)
tests/              - All test files
scripts/            - One-off utility scripts
reports/            - Generated report output (gitignored)
```

Do not create new top-level directories or reorganise the structure without explicit instruction.

## What NOT To Do

- Do not add WebSocket or real-time streaming code
- Do not add trade execution capability (open/close positions)
- Do not add a web UI or dashboard
- Do not add notification systems (email, Telegram, etc.)
- Do not add async/await patterns - keep everything synchronous
- Do not add dependencies not listed in `PLAN.md` section 5 without explicit approval
- Do not modify the SurrealDB schema without updating `PLAN.md` section 3 to match
- Do not create documentation files beyond what already exists (PLAN.md, AGENTS.md)
- Do not skip writing tests for any new functionality
- **Do NOT edit unit tests to make them pass** - fix the implementation code instead. Tests define the expected behaviour; if a test fails, the implementation is wrong, not the test.

## Work Tracker

`PLAN.md` section 10 contains a **Work Tracker** table that maps every PR to its roadmap steps, deliverables, and current status. This table must be kept up to date:

- When starting work on a PR, set its status to `In Progress`
- When opening the PR for review, set its status to `In Review`
- When the PR is merged, set its status to `Done`
- If a PR is split into smaller PRs, update the table to reflect the new breakdown
- Never remove rows from the table - only add or update them

### Definition of Done

A PR may only be marked as `Done` when **all** of the following are met:

1. **Acceptance criteria** - every checkbox in the step's acceptance criteria (in `PLAN.md` section 7) is satisfied
2. **Tests pass** - `pytest` exits 0 with no failures or errors
3. **No regressions** - all previously passing tests still pass
4. **Code review** - PR has been reviewed and merged to `main`

Do not mark a PR as `Done` if any acceptance criterion is unmet, even if the code is merged. Flag the gap and address it before updating the status.

## Commit Conventions

- Use conventional commit style: `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`
- Keep commits focused - one logical change per commit
- Reference the roadmap step in commit messages where relevant (e.g. `feat: implement eToro auth client (step 2)`)

## File Naming

- Python source files use `snake_case.py`
- Test files are named `test_<module>.py`
- No abbreviations in file names - use full words (`instruments.py` not `instr.py`)

## Dependencies

When this file was written, the intended dependencies are:

| Package | Purpose |
|---|---|
| `httpx` | HTTP client for eToro API |
| `surrealdb` | SurrealDB Python SDK |
| `pydantic` | Data validation and models |
| `pydantic-settings` | Environment variable loading |
| `pandas` | Data manipulation for analysis |
| `structlog` | Structured logging |
| `openai` or `anthropic` | LLM API for market commentary |
| `pytest` | Testing |
| `pytest-httpx` | HTTP mocking in tests |
| `rich` | Terminal output formatting |

Do not add packages beyond this list without explicit approval. If a task can be accomplished with the standard library or an existing dependency, prefer that over adding a new one.
