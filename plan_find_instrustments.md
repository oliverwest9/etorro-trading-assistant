# Plan: Multi-Agent Instrument Discovery & Fund Allocation

## 1. Objective

Design a multi-agent system that identifies promising new assets for capital allocation. The system should:

- Scan the market to identify broad sectors and themes with positive momentum
- Delegate fine-grained investigation of individual instruments to specialist sub-agents
- Assess how candidates fit into the existing portfolio (diversification, risk, correlation)
- Produce ranked recommendations with suggested position sizes
- Integrate seamlessly with the existing advisory pipeline (no trade execution)

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Discovery Orchestrator                         │
│                                                                     │
│  Reads: portfolio snapshot, existing positions, market universe     │
│  Decides: which sectors/themes to investigate                       │
│  Delegates: sector deep-dives to sub-agents                        │
│  Collects: candidate instruments from all sub-agents               │
│  Outputs: ranked shortlist → Portfolio Fit Agent → Allocation Agent │
└───────────┬──────────────┬──────────────┬──────────────┬───────────┘
            │              │              │              │
   ┌────────▼───────┐ ┌───▼──────────┐ ┌▼───────────┐ ┌▼───────────┐
   │ Sector Scout   │ │ Sector Scout │ │Sector Scout│ │Sector Scout│
   │ (Tech/Growth)  │ │ (Energy)     │ │(Healthcare)│ │(Crypto)    │
   │                │ │              │ │            │ │            │
   │ Searches eToro │ │ Searches ... │ │ Searches ..│ │ Searches ..│
   │ instruments in │ │              │ │            │ │            │
   │ this sector,   │ │              │ │            │ │            │
   │ runs technical │ │              │ │            │ │            │
   │ analysis on    │ │              │ │            │ │            │
   │ each candidate │ │              │ │            │ │            │
   └────────┬───────┘ └───┬──────────┘ └┬───────────┘ └┬───────────┘
            │              │              │              │
            └──────────────┴──────┬───────┴──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │     Portfolio Fit Agent     │
                    │                            │
                    │ Checks: diversification,   │
                    │ correlation with existing  │
                    │ positions, sector balance  │
                    │ Filters: rejects poor fits │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │     Allocation Agent        │
                    │                            │
                    │ Calculates: position sizes,│
                    │ entry price targets,       │
                    │ risk/reward ratios         │
                    │ Outputs: final ranked list │
                    └────────────────────────────┘
```

---

## 3. Agent Definitions

### 3.1 Discovery Orchestrator

**Role:** Top-level coordinator. Decides _where_ to look and delegates the actual searching.

**Inputs:**
- Current portfolio snapshot (positions, cash, sector breakdown)
- Previous discovery results (from SurrealDB `discovery_run` table)
- Market-wide sector performance data (broad indices or ETF proxies)
- Configurable parameters: max sectors to scan, max candidates per sector

**Process:**
1. **Gap analysis** — Examine the current portfolio's sector allocation. Identify under-represented sectors or asset classes (e.g. the portfolio is 60% tech and 0% healthcare).
2. **Macro signal scan** — Use sector-level ETFs or indices as proxies to identify sectors with positive momentum over 3–6 months. Rank sectors by risk-adjusted return.
3. **Theme identification** — Optionally incorporate LLM reasoning (Gemini) to identify macro themes worth investigating (e.g. "AI infrastructure", "energy transition", "emerging market recovery"). The LLM would receive sector performance data and news context as input.
4. **Delegation** — For each promising sector/theme (top N, configurable), spawn a Sector Scout sub-agent with specific search criteria.
5. **Aggregation** — Collect candidate lists from all scouts, deduplicate, and pass the merged list to the Portfolio Fit Agent.

**LLM usage:** Optional. The orchestrator can work in two modes:
- **Procedural mode** — Rules-based sector ranking (momentum + gap analysis), no LLM needed
- **LLM-assisted mode** — Gemini provides thematic reasoning alongside quantitative signals

**Configuration (stored in SurrealDB `config` table):**
```json
{
  "key": "discovery_settings",
  "value": {
    "max_sectors": 4,
    "max_candidates_per_sector": 10,
    "min_sector_momentum_pct": 2.0,
    "lookback_days": 90,
    "excluded_instruments": [12345, 67890],
    "excluded_sectors": [],
    "preferred_asset_classes": ["stock", "etf"],
    "run_frequency": "weekly"
  }
}
```

---

### 3.2 Sector Scout (Sub-Agent, one per sector)

**Role:** Deep-dive into a single sector or theme. Searches for instruments, fetches price data, runs technical analysis, and returns a ranked candidate list.

**Inputs (from orchestrator):**
- Sector/theme name and search keywords
- Asset class filter (stock, ETF, crypto, commodity)
- Lookback period for candle data
- Max candidates to return
- List of instruments already in the portfolio (to exclude)

**Process:**
1. **Search** — Call `search_instruments()` on the eToro API with sector/keyword filters. Retrieve all instruments matching the criteria.
2. **Filter** — Remove instruments already held in the portfolio. Remove instruments with low liquidity or that are suspended.
3. **Fetch candles** — For each remaining candidate, fetch daily candles for the lookback period (e.g. 90 days).
4. **Technical analysis** — Run the existing indicator registry (trend, momentum, levels) on each candidate's candle data. Reuse `analyse_price_action()` directly.
5. **Score** — Compute a composite score per candidate:
   - Trend strength (bullish = higher score)
   - Momentum signal (positive = higher score)
   - Proximity to support (closer to support = better entry)
   - Volatility penalty (excessively volatile instruments score lower)
6. **Rank and return** — Return the top N candidates sorted by composite score, each with:
   - Instrument metadata (symbol, name, exchange, asset class)
   - Technical analysis summary (trend, momentum, support/resistance levels)
   - Composite score and score breakdown
   - Current price and 90-day performance

**Tools (reused from existing codebase):**
- `search_instruments()` (from `src/agent/etoro/market_data.py`)
- `fetch_candles()` (from `src/agent/etoro/market_data.py`)
- `analyse_price_action()` (from `src/agent/analysis/price_action.py`)
- `compute_risk_metrics()` (from `src/agent/analysis/critic.py`)

**Parallelisation:** Multiple Sector Scouts can run concurrently since they operate on independent sectors. The orchestrator should dispatch them in parallel where possible.

**Example scout prompt (if LLM-assisted):**
> You are investigating the **Healthcare** sector for long-term investment candidates. You have access to eToro's instrument catalogue and technical analysis tools. Find the strongest candidates based on trend, momentum, and proximity to support levels. Exclude instruments already in the portfolio. Return your top 10 ranked by overall attractiveness.

---

### 3.3 Portfolio Fit Agent

**Role:** Evaluate how each candidate instrument would affect the overall portfolio if added. Filter out instruments that would worsen diversification or introduce excessive correlation.

**Inputs:**
- Merged candidate list from all Sector Scouts
- Current portfolio snapshot (positions, values, sector breakdown)
- Existing risk assessment (from the financial specialist's `critic.py` output)

**Process:**
1. **Sector concentration check** — For each candidate, simulate adding it to the portfolio. Recalculate the Herfindahl-Hirschman Index (HHI). Reject candidates that would push HHI above the "concentrated" threshold.
2. **Correlation estimate** — Compare the candidate's price history against the portfolio's largest positions. Flag candidates with >0.8 correlation to any existing holding (they add risk, not diversification).
3. **Asset class balance** — Check whether the candidate improves the stock/ETF/crypto/commodity mix or further skews it.
4. **Redundancy check** — If the portfolio already holds 3 tech stocks and the candidate is a 4th tech stock in the same sub-sector, flag it as redundant.
5. **Output** — A filtered list of candidates that pass all portfolio fit criteria, annotated with:
   - Diversification impact (improves / neutral / worsens)
   - Correlation with top-3 existing positions
   - Sector balance impact

**Tools (reused):**
- `assess_diversification()` (from `src/agent/analysis/critic.py`)
- HHI calculation (existing in `critic.py`)
- New: `estimate_correlation(candles_a, candles_b)` — simple Pearson correlation of daily returns

---

### 3.4 Allocation Agent

**Role:** Determine how much capital to allocate to each approved candidate. Produce the final ranked recommendation list with actionable position sizes.

**Inputs:**
- Filtered candidate list (from Portfolio Fit Agent)
- Available cash in the portfolio
- Risk tolerance parameters (from config)
- Current portfolio total value

**Process:**
1. **Budget calculation** — Determine the maximum capital available for new positions. This is a configurable percentage of available cash (e.g. deploy up to 60% of cash, keep 40% as buffer).
2. **Position sizing** — For each candidate, calculate:
   - Maximum position size = min(budget / num_candidates, 15% of portfolio value)
   - Volatility-adjusted size = base size × (target_volatility / candidate_volatility)
   - The more volatile the instrument, the smaller the position
3. **Priority ranking** — Rank candidates by:
   - Composite technical score (from Sector Scout)
   - Portfolio fit score (from Portfolio Fit Agent)
   - Risk-adjusted expected return
4. **Entry targets** — For each candidate, suggest:
   - Ideal entry price (near support levels from technical analysis)
   - Maximum acceptable entry price
   - Stop-loss level (below nearest support)
5. **Output** — Final recommendation list:

```
Rank | Symbol | Sector    | Score | Action     | Size (£) | Entry Target | Stop Loss | Conviction
1    | NOVO   | Healthcare| 8.2   | Accumulate | £500     | £85.20       | £78.50    | High
2    | ASML   | Tech      | 7.8   | Accumulate | £400     | £620.00      | £580.00   | Medium
3    | RIO    | Mining    | 7.1   | Accumulate | £300     | £52.10       | £48.00    | Medium
```

**Configuration:**
```json
{
  "key": "allocation_settings",
  "value": {
    "max_cash_deploy_pct": 0.60,
    "max_single_position_pct": 0.15,
    "target_annual_volatility": 0.20,
    "min_conviction_threshold": "medium",
    "prefer_limit_orders": true
  }
}
```

---

## 4. Data Model (New SurrealDB Tables)

### 4.1 `discovery_run`

Tracks each execution of the discovery pipeline.

```surql
DEFINE TABLE discovery_run SCHEMAFULL;
DEFINE FIELD run_id ON discovery_run TYPE string;          -- UUID v4, generated by orchestrator
DEFINE FIELD run_type ON discovery_run TYPE string;          -- "scheduled" | "manual"
DEFINE FIELD sectors_scanned ON discovery_run TYPE array<string>;
DEFINE FIELD candidates_found ON discovery_run TYPE int;
DEFINE FIELD candidates_approved ON discovery_run TYPE int;
DEFINE FIELD config_snapshot ON discovery_run TYPE object;   -- settings at time of run
DEFINE FIELD started_at ON discovery_run TYPE datetime;
DEFINE FIELD completed_at ON discovery_run TYPE option<datetime>;
DEFINE FIELD status ON discovery_run TYPE string;            -- "running" | "completed" | "failed"
DEFINE INDEX idx_discovery_run_id ON discovery_run FIELDS run_id UNIQUE;
```

### 4.2 `candidate`

Individual instrument candidates produced by Sector Scouts, enriched by downstream agents.

```surql
DEFINE TABLE candidate SCHEMAFULL;
DEFINE FIELD discovery_run ON candidate TYPE record<discovery_run>;
DEFINE FIELD instrument ON candidate TYPE record<instrument>;
DEFINE FIELD sector ON candidate TYPE string;
DEFINE FIELD source_scout ON candidate TYPE string;          -- scout identifier

-- Technical analysis outputs
DEFINE FIELD trend ON candidate TYPE string;
DEFINE FIELD trend_strength ON candidate TYPE float;
DEFINE FIELD momentum_signal ON candidate TYPE string;
DEFINE FIELD support_level ON candidate TYPE option<float>;
DEFINE FIELD resistance_level ON candidate TYPE option<float>;
DEFINE FIELD annualised_volatility ON candidate TYPE option<float>;
DEFINE FIELD simple_return_pct ON candidate TYPE option<float>;
DEFINE FIELD composite_score ON candidate TYPE float;

-- Portfolio fit outputs (populated by Portfolio Fit Agent)
DEFINE FIELD diversification_impact ON candidate TYPE option<string>;  -- "improves" | "neutral" | "worsens"
DEFINE FIELD correlation_top3 ON candidate TYPE option<float>;         -- max correlation with any of the top 3 existing positions
DEFINE FIELD portfolio_fit_pass ON candidate TYPE option<bool>;

-- Allocation outputs (populated by Allocation Agent)
DEFINE FIELD recommended_action ON candidate TYPE option<string>;
DEFINE FIELD recommended_size ON candidate TYPE option<float>;
DEFINE FIELD entry_target ON candidate TYPE option<float>;
DEFINE FIELD stop_loss ON candidate TYPE option<float>;
DEFINE FIELD conviction ON candidate TYPE option<string>;
DEFINE FIELD final_rank ON candidate TYPE option<int>;

DEFINE FIELD created_at ON candidate TYPE datetime DEFAULT time::now();
DEFINE INDEX idx_candidate_run ON candidate FIELDS discovery_run;
```

---

## 5. Integration with Existing Pipeline

### 5.1 New Specialists

The discovery system introduces **four new specialists** that follow the existing `BaseSpecialist` pattern:

| Specialist | Name | Procedural? | LLM-Assisted? |
|-----------|------|-------------|----------------|
| Discovery Orchestrator | `discovery` | Yes (with optional LLM) | Optional |
| Sector Scout | `sector_scout` | Yes | No |
| Portfolio Fit | `portfolio_fit` | Yes | No |
| Allocation | `allocation` | Yes | No |

Each specialist implements:
- `name` / `description` properties
- `create_tools(ctx)` — returns LangChain tools wrapping existing analysis/API functions
- `run_procedural(state, ctx)` — deterministic execution path
- `process_results(state, ctx)` — writes results to SurrealDB, updates pipeline state

### 5.2 Pipeline State Extensions

New fields added to the discovery pipeline state:

```python
class DiscoveryState(TypedDict, total=False):
    run_id: str
    portfolio_snapshot_id: str
    sectors_to_scan: list[str]
    scout_results: dict[str, list[dict]]   # sector → candidate list
    fit_results: list[dict]                 # candidates that pass fit
    allocation_results: list[dict]          # final ranked recommendations
    errors: list[dict]
    start_time: float
    duration_ms: int
```

### 5.3 Trigger Modes

The discovery pipeline runs separately from the main advisory pipeline:

1. **Scheduled (weekly)** — Cron job or manual invocation: `python -m agent.main --discovery`
2. **Manual** — User triggers via CLI: `python -m agent.main --discovery --sectors "healthcare,energy"`
3. **Post-report hook** — Optionally triggered after the main advisory run if cash allocation exceeds a configurable threshold (e.g. >30% cash → run discovery)

### 5.4 Report Integration

Discovery results are included in the main advisory report as an optional section:

```markdown
## 📡 New Instrument Opportunities

Last discovery run: 2026-03-15 (4 sectors scanned, 12 candidates evaluated)

### Top Recommendations

| Rank | Symbol | Sector     | Score | Action     | Size   | Entry   | Conviction |
|------|--------|------------|-------|------------|--------|---------|------------|
| 1    | NOVO   | Healthcare | 8.2   | Accumulate | £500   | £85.20  | High       |
| 2    | ASML   | Tech       | 7.8   | Accumulate | £400   | £620.00 | Medium     |

### Sector Scan Summary
- **Healthcare**: 3 candidates found, 2 passed portfolio fit
- **Energy**: 4 candidates found, 1 passed portfolio fit
- **Mining**: 2 candidates found, 1 passed portfolio fit
- **Emerging Markets**: 3 candidates found, 0 passed portfolio fit (high correlation)
```

---

## 6. Execution Flow (Detailed)

```
1. CLI invocation: python -m agent.main --discovery

2. Load Settings, connect to SurrealDB, create EToroClient

3. Discovery Orchestrator starts
   ├── Read current portfolio snapshot from DB
   ├── Read previous discovery runs from DB (avoid re-scanning recent sectors)
   ├── Compute sector gap analysis:
   │   ├── Current allocation: Tech 55%, Finance 20%, Energy 10%, Cash 15%
   │   ├── Under-represented: Healthcare (0%), Mining (0%), Consumer (0%)
   │   └── Over-represented: Tech (55% > 30% threshold)
   ├── Fetch sector ETF/index performance (via eToro candles):
   │   ├── XLV (Healthcare ETF): +8.2% over 90 days, bullish trend
   │   ├── XLE (Energy ETF): +3.1% over 90 days, neutral trend
   │   ├── XLK (Tech ETF): +12.5% over 90 days, bullish (already overweight)
   │   └── GDX (Mining ETF): +5.7% over 90 days, bullish trend
   ├── Rank sectors by: gap_score × momentum_score
   │   1. Healthcare (big gap + strong momentum) → SCAN
   │   2. Mining (big gap + decent momentum) → SCAN
   │   3. Energy (medium gap + weak momentum) → SCAN
   │   4. Tech (no gap, overweight) → SKIP
   └── Dispatch 3 Sector Scouts

4. Sector Scout: Healthcare
   ├── search_instruments(keywords=["healthcare","pharma","biotech"], asset_class="stock")
   ├── Filter out already-held instruments
   ├── Fetch 90-day candles for top 20 matches
   ├── Run analyse_price_action() on each
   ├── Run compute_risk_metrics() on each
   ├── Score and rank
   └── Return top 10 candidates

5. Sector Scout: Mining (parallel with Healthcare)
   └── ... same process ...

6. Sector Scout: Energy (parallel)
   └── ... same process ...

7. Discovery Orchestrator aggregates all candidates (e.g. 25 total)

8. Portfolio Fit Agent
   ├── For each candidate:
   │   ├── Simulate adding to portfolio
   │   ├── Check HHI impact
   │   ├── Estimate correlation with existing holdings
   │   ├── Check sector balance
   │   └── Pass/fail decision
   └── Output: 10 candidates that pass fit criteria

9. Allocation Agent
   ├── Calculate deployable cash budget (e.g. 60% of £2,000 cash = £1,200)
   ├── For each approved candidate:
   │   ├── Volatility-adjusted position size
   │   ├── Entry target price (near support)
   │   ├── Stop-loss level
   │   └── Conviction rating
   ├── Rank by composite score × fit score
   └── Output: final 5-8 recommendations within budget

10. Store results in SurrealDB (discovery_run + candidate records)

11. Generate discovery report section (markdown)

12. Output to terminal + save to reports/ directory
```

---

## 7. Scoring Model

### 7.1 Sector Ranking (Orchestrator)

Each sector receives a score from 0 to 10:

```
sector_score = (gap_weight × gap_score) + (momentum_weight × momentum_score)

Where:
  gap_score      = 1.0 if sector has 0% allocation, scales down to 0.0 at 30%+
  momentum_score = normalised 90-day return of sector proxy ETF (0.0 to 1.0)
  gap_weight     = 0.6  (prioritise filling gaps)
  momentum_weight = 0.4  (but only if the sector is actually performing)
```

### 7.2 Instrument Scoring (Sector Scout)

Each candidate instrument receives a composite score from 0 to 10:

```
composite_score = (
    trend_score × 0.30 +          # Bullish trend = higher
    momentum_score × 0.25 +       # Positive momentum = higher
    support_proximity × 0.20 +    # Closer to support = better entry
    volatility_penalty × 0.15 +   # Lower volatility = higher score
    return_score × 0.10           # Recent return (reward, not chase)
)

Where:
  trend_score        = trend_strength if bullish, 0.0 if bearish (bearish instruments
                       should generally be excluded by the scout before scoring, but
                       this provides a fallback for edge cases like neutral-to-bearish)
  momentum_score     = normalised momentum indicator (0.0 to 1.0)
  support_proximity  = 1.0 if price is at support, 0.0 if at resistance
  volatility_penalty = 1.0 - min(annualised_vol / 0.60, 1.0)  # penalise >60% vol
  return_score       = normalised 90-day return (0.0 to 1.0)
```

### 7.3 Final Ranking (Allocation Agent)

```
final_score = composite_score × 0.60 + portfolio_fit_score × 0.40

Where:
  portfolio_fit_score = weighted sum of:
    - diversification_impact:  improves=1.0, neutral=0.5, worsens=0.0
    - correlation_penalty:     1.0 - max_correlation_with_existing
    - sector_balance_bonus:    1.0 if fills a gap, 0.5 if neutral, 0.0 if overweight
```

---

## 8. Configuration & Tuning

All discovery parameters are stored in the SurrealDB `config` table and can be tuned without code changes:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_sectors` | 4 | Maximum sectors to scan per run |
| `max_candidates_per_sector` | 10 | Candidates returned per scout |
| `lookback_days` | 90 | Candle history for analysis |
| `min_sector_momentum_pct` | 2.0 | Minimum 90-day sector return to consider |
| `max_cash_deploy_pct` | 0.60 | Maximum % of cash to allocate |
| `max_single_position_pct` | 0.15 | Maximum % of portfolio per position |
| `target_annual_volatility` | 0.20 | Target vol for position sizing |
| `correlation_threshold` | 0.80 | Reject candidates above this correlation |
| `hhi_max_threshold` | 0.25 | Reject if adding the instrument pushes HHI above this |
| `min_conviction` | "medium" | Minimum conviction to include in final list |
| `excluded_instruments` | [] | Instrument IDs to always skip |
| `preferred_asset_classes` | ["stock", "etf"] | Asset classes to search |
| `sector_gap_weight` | 0.6 | Weight for gap analysis in sector ranking |
| `sector_momentum_weight` | 0.4 | Weight for momentum in sector ranking |

---

## 9. Error Handling & Resilience

- **Scout failure isolation** — If one Sector Scout fails (e.g. eToro API timeout for that sector's instruments), the other scouts continue. The orchestrator logs the failure and proceeds with partial results.
- **Empty results** — If no candidates pass the Portfolio Fit Agent, the discovery run completes with zero recommendations (this is a valid outcome, not an error).
- **Rate limiting** — Sector Scouts that fetch candles for many instruments should respect eToro API rate limits. Use the existing retry/backoff in `EToroClient`.
- **Idempotency** — Re-running discovery for the same week should not create duplicate candidates. Use the `discovery_run` + instrument unique index to deduplicate.
- **Graceful degradation** — If the LLM is unavailable, the orchestrator falls back to purely procedural sector ranking (momentum + gap analysis only).

---

## 10. Testing Strategy

| Test Type | What to Test | Approach |
|-----------|-------------|----------|
| **Unit** | Sector scoring formula | Pure function, deterministic inputs |
| **Unit** | Instrument composite scoring | Pure function, deterministic inputs |
| **Unit** | Position sizing calculation | Pure function, various cash/vol scenarios |
| **Unit** | Portfolio fit checks (HHI, correlation) | Reuse existing `critic.py` test patterns |
| **Integration** | Full discovery pipeline (orchestrator → scouts → fit → allocation) | Mock eToro API, in-memory SurrealDB |
| **Integration** | Discovery report generation | Verify markdown output format |
| **Edge case** | Zero candidates found | Verify graceful empty result |
| **Edge case** | All candidates rejected by fit agent | Verify zero-recommendation output |
| **Edge case** | Single sector available | Verify single-scout dispatch |

---

## 11. Implementation Phases

### Phase 1: Foundation
- [ ] Define `DiscoveryState` TypedDict
- [ ] Create `discovery_run` and `candidate` SurrealDB tables (schema + migration)
- [ ] Create DB access functions: `create_discovery_run()`, `upsert_candidate()`, `get_latest_discovery()`
- [ ] Add `--discovery` CLI flag to `main.py`
- [ ] Write unit tests for new DB functions

### Phase 2: Sector Scout
- [ ] Implement sector scoring formula (pure function)
- [ ] Implement instrument composite scoring (pure function)
- [ ] Create `SectorScoutSpecialist` extending `BaseSpecialist`
- [ ] Wire up existing `search_instruments()`, `fetch_candles()`, `analyse_price_action()` as tools
- [ ] Write unit tests for scoring functions
- [ ] Write integration test for a single scout run (mocked API)

### Phase 3: Discovery Orchestrator
- [ ] Implement gap analysis logic (compare portfolio vs universe)
- [ ] Implement sector proxy lookup (map sectors to ETF instrument IDs)
- [ ] Create `DiscoveryOrchestratorSpecialist` extending `BaseSpecialist`
- [ ] Implement procedural routing (dispatch N scouts based on sector scores)
- [ ] Optional: Add LLM-assisted theme identification mode
- [ ] Write unit tests for gap analysis and sector ranking
- [ ] Write integration test for orchestrator dispatching scouts

### Phase 4: Portfolio Fit Agent
- [ ] Implement correlation estimation (Pearson on daily returns)
- [ ] Implement simulated HHI recalculation with candidate added
- [ ] Create `PortfolioFitSpecialist` extending `BaseSpecialist`
- [ ] Wire up existing `assess_diversification()` from `critic.py`
- [ ] Write unit tests for correlation and fit checks
- [ ] Write integration test for fit filtering

### Phase 5: Allocation Agent
- [ ] Implement volatility-adjusted position sizing
- [ ] Implement entry target / stop-loss calculation from support/resistance levels
- [ ] Implement budget allocation across multiple candidates
- [ ] Create `AllocationSpecialist` extending `BaseSpecialist`
- [ ] Write unit tests for sizing and allocation logic
- [ ] Write integration test for full allocation run

### Phase 6: Integration & Reporting
- [ ] Wire all four specialists into a `discovery_graph` (LangGraph)
- [ ] Add discovery report section to `formatter.py`
- [ ] Add post-report hook (optional: trigger discovery if cash > threshold)
- [ ] Write full end-to-end integration test (mocked API, in-memory DB)
- [ ] Manual testing with real eToro sandbox data

---

## 12. Open Questions

1. **Sector proxy mapping** — Which eToro instruments best represent each sector for momentum scanning? Need to build a mapping table (e.g. XLV → Healthcare, XLE → Energy). Should this be hardcoded or configurable?
2. **Scout parallelisation** — The current pipeline is synchronous (no async/await per project convention). Should Sector Scouts run sequentially (simpler, consistent with existing patterns) or use `concurrent.futures.ThreadPoolExecutor` for parallel execution (faster, still synchronous, but more complex)?
3. **Discovery frequency** — Weekly seems right for long-term focus, but should it also run when cash allocation exceeds a threshold after a sell recommendation?
4. **Correlation window** — 90 days of daily returns for correlation? Or should this match the main analysis lookback period?
5. **Crypto handling** — Crypto instruments don't have sectors in the traditional sense. Should crypto be a special-case scout with different search criteria?
