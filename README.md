# eToro Trading Assistant

An advisory trading agent that integrates with eToro's public API. It runs twice daily (UK market open and close), analyses market data and portfolio positions, and generates a report with recommended actions and market commentary. It does **not** execute trades automatically — it is read-only.

## Features

- Fetches live portfolio and market data from eToro's public API
- Analyses price action using technical indicators (trend, momentum, support/resistance)
- Groups and compares sector performance
- Uses an LLM to generate natural-language market commentary
- Produces formatted terminal reports and markdown files
- Persists data in SurrealDB for historical tracking

## Requirements

- Python 3.11+
- SurrealDB (via Docker or embedded)
- eToro API credentials
- LLM API key (OpenAI, Anthropic, or Google Gemini)

## Setup

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Copy environment template and fill in credentials
cp .env.example .env

# Start SurrealDB
docker-compose up -d

# Initialise the database schema
python scripts/init_db.py
```

## Usage

```bash
# Run the full pipeline (default: market_open)
python scripts/run_pipeline.py

# Run for market close
python scripts/run_pipeline.py market_close

# Run with verbose output
python scripts/run_pipeline.py --verbose
```

Reports are saved to the `reports/` directory.

## Testing

```bash
# Run all tests
pytest

# Run unit tests only
pytest -m "not integration"

# Run integration tests only
pytest -m integration
```
