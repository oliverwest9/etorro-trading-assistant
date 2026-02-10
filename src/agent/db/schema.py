"""SurrealDB schema definition and application.

The schema is defined once here as a SurrealQL string and applied via
``apply_schema()``.  Using ``DEFINE … OVERWRITE`` makes repeated application
idempotent — running the schema twice has no ill-effects.

The schema mirrors section 3 of ``PLAN.md``.  Any changes here **must** be
reflected in ``PLAN.md`` and vice-versa.
"""

from __future__ import annotations

import structlog
from surrealdb.connections.sync_template import SyncTemplate

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Schema string (SurrealQL)
# ---------------------------------------------------------------------------
# ``DEFINE … OVERWRITE`` (available since SurrealDB v2.0.0) makes every
# statement idempotent — safe to re-run at any time.

SCHEMA = """\
-- ============================================================
-- INSTRUMENTS
-- ============================================================
DEFINE TABLE OVERWRITE instrument SCHEMAFULL;
DEFINE FIELD OVERWRITE etoro_id        ON instrument TYPE int;
DEFINE FIELD OVERWRITE symbol          ON instrument TYPE string;
DEFINE FIELD OVERWRITE name            ON instrument TYPE string;
DEFINE FIELD OVERWRITE asset_class     ON instrument TYPE string;
DEFINE FIELD OVERWRITE exchange        ON instrument TYPE option<string>;
DEFINE FIELD OVERWRITE industry        ON instrument TYPE option<string>;
DEFINE FIELD OVERWRITE is_active       ON instrument TYPE bool          DEFAULT true;
DEFINE FIELD OVERWRITE metadata        ON instrument TYPE option<object>;
DEFINE FIELD OVERWRITE updated_at      ON instrument TYPE datetime      DEFAULT time::now();
DEFINE INDEX OVERWRITE idx_symbol      ON instrument FIELDS symbol      UNIQUE;
DEFINE INDEX OVERWRITE idx_etoro_id    ON instrument FIELDS etoro_id    UNIQUE;

-- ============================================================
-- OHLCV CANDLES
-- ============================================================
DEFINE TABLE OVERWRITE candle SCHEMAFULL;
DEFINE FIELD OVERWRITE instrument      ON candle TYPE record<instrument>;
DEFINE FIELD OVERWRITE timeframe       ON candle TYPE string;
DEFINE FIELD OVERWRITE open            ON candle TYPE float;
DEFINE FIELD OVERWRITE high            ON candle TYPE float;
DEFINE FIELD OVERWRITE low             ON candle TYPE float;
DEFINE FIELD OVERWRITE close           ON candle TYPE float;
DEFINE FIELD OVERWRITE volume          ON candle TYPE option<float>;
DEFINE FIELD OVERWRITE timestamp       ON candle TYPE datetime;
DEFINE INDEX OVERWRITE idx_candle_lookup ON candle FIELDS instrument, timeframe, timestamp UNIQUE;

-- ============================================================
-- PORTFOLIO SNAPSHOTS
-- ============================================================
DEFINE TABLE OVERWRITE portfolio_snapshot SCHEMAFULL;
DEFINE FIELD OVERWRITE total_value     ON portfolio_snapshot TYPE float;
DEFINE FIELD OVERWRITE cash_available  ON portfolio_snapshot TYPE float;
DEFINE FIELD OVERWRITE open_positions  ON portfolio_snapshot TYPE int;
DEFINE FIELD OVERWRITE total_pnl       ON portfolio_snapshot TYPE float;
DEFINE FIELD OVERWRITE positions       ON portfolio_snapshot TYPE array;
DEFINE FIELD OVERWRITE positions.*     ON portfolio_snapshot FLEXIBLE TYPE object;
DEFINE FIELD OVERWRITE run_type        ON portfolio_snapshot TYPE string;
DEFINE FIELD OVERWRITE captured_at     ON portfolio_snapshot TYPE datetime DEFAULT time::now();

-- ============================================================
-- ANALYSIS RESULTS (per instrument per run)
-- ============================================================
DEFINE TABLE OVERWRITE analysis SCHEMAFULL;
DEFINE FIELD OVERWRITE instrument      ON analysis TYPE record<instrument>;
DEFINE FIELD OVERWRITE run_id          ON analysis TYPE string;
DEFINE FIELD OVERWRITE trend           ON analysis TYPE string;
DEFINE FIELD OVERWRITE trend_strength  ON analysis TYPE float;
DEFINE FIELD OVERWRITE price_action    ON analysis TYPE object;
DEFINE FIELD OVERWRITE sector_context  ON analysis TYPE option<object>;
DEFINE FIELD OVERWRITE raw_data        ON analysis TYPE object;
DEFINE FIELD OVERWRITE created_at      ON analysis TYPE datetime          DEFAULT time::now();

-- ============================================================
-- REPORTS (the final output)
-- ============================================================
DEFINE TABLE OVERWRITE report SCHEMAFULL;
DEFINE FIELD OVERWRITE run_id          ON report TYPE string;
DEFINE FIELD OVERWRITE run_type        ON report TYPE string;
DEFINE FIELD OVERWRITE portfolio_snapshot ON report TYPE record<portfolio_snapshot>;
DEFINE FIELD OVERWRITE recommendations ON report TYPE array;
DEFINE FIELD OVERWRITE recommendations.* ON report FLEXIBLE TYPE object;
DEFINE FIELD OVERWRITE commentary      ON report TYPE string;
DEFINE FIELD OVERWRITE summary         ON report TYPE string;
DEFINE FIELD OVERWRITE report_markdown ON report TYPE string;
DEFINE FIELD OVERWRITE created_at      ON report TYPE datetime            DEFAULT time::now();
DEFINE INDEX OVERWRITE idx_run_id      ON report FIELDS run_id            UNIQUE;

-- ============================================================
-- RECOMMENDATIONS (individual actions within a report)
-- ============================================================
DEFINE TABLE OVERWRITE recommendation SCHEMAFULL;
DEFINE FIELD OVERWRITE report          ON recommendation TYPE record<report>;
DEFINE FIELD OVERWRITE instrument      ON recommendation TYPE record<instrument>;
DEFINE FIELD OVERWRITE action          ON recommendation TYPE string;
DEFINE FIELD OVERWRITE conviction      ON recommendation TYPE string;
DEFINE FIELD OVERWRITE reasoning       ON recommendation TYPE string;
DEFINE FIELD OVERWRITE analysis        ON recommendation TYPE record<analysis>;
DEFINE FIELD OVERWRITE created_at      ON recommendation TYPE datetime    DEFAULT time::now();

-- ============================================================
-- AGENT RUN LOG (audit trail)
-- ============================================================
DEFINE TABLE OVERWRITE run_log SCHEMAFULL;
DEFINE FIELD OVERWRITE run_id          ON run_log TYPE string;
DEFINE FIELD OVERWRITE run_type        ON run_log TYPE string;
DEFINE FIELD OVERWRITE status          ON run_log TYPE string;
DEFINE FIELD OVERWRITE instruments_analysed ON run_log TYPE int;
DEFINE FIELD OVERWRITE recommendations_made ON run_log TYPE int;
DEFINE FIELD OVERWRITE errors          ON run_log TYPE option<array>;
DEFINE FIELD OVERWRITE errors.*        ON run_log FLEXIBLE TYPE object;
DEFINE FIELD OVERWRITE duration_ms     ON run_log TYPE option<int>;
DEFINE FIELD OVERWRITE started_at      ON run_log TYPE datetime           DEFAULT time::now();
DEFINE FIELD OVERWRITE completed_at    ON run_log TYPE option<datetime>;

-- ============================================================
-- CONFIGURATION
-- ============================================================
DEFINE TABLE OVERWRITE config SCHEMAFULL;
DEFINE FIELD OVERWRITE key             ON config TYPE string;
DEFINE FIELD OVERWRITE value           ON config TYPE object;
DEFINE FIELD OVERWRITE updated_at      ON config TYPE datetime            DEFAULT time::now();
DEFINE INDEX OVERWRITE idx_config_key  ON config FIELDS key               UNIQUE;
"""

# Tables and indexes the schema is expected to create.  Used by tests and
# the ``verify_schema()`` helper.
EXPECTED_TABLES = frozenset(
    {
        "instrument",
        "candle",
        "portfolio_snapshot",
        "analysis",
        "report",
        "recommendation",
        "run_log",
        "config",
    }
)

EXPECTED_INDEXES = frozenset(
    {
        "idx_symbol",
        "idx_etoro_id",
        "idx_candle_lookup",
        "idx_run_id",
        "idx_config_key",
    }
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def apply_schema(db: SyncTemplate) -> None:
    """Execute the full SurrealQL schema against an open connection.

    Because every ``DEFINE`` uses the ``OVERWRITE`` clause, calling this
    function multiple times is idempotent.

    Args:
        db: An authenticated, namespace/database-selected Surreal connection.
    """
    logger.info("schema_applying")
    db.query(SCHEMA)
    logger.info("schema_applied")
