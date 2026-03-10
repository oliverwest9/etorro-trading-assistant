"""Pipeline state definition for the LangGraph agent pipeline.

The ``PipelineState`` TypedDict defines the shared state that flows
through all nodes in the agent graph.  Each specialist agent reads
from and writes to this state.
"""

from __future__ import annotations

from typing import Any, TypedDict


class PipelineState(TypedDict, total=False):
    """Shared state flowing through the LangGraph pipeline.

    All fields are optional (``total=False``) so that nodes only need
    to return the keys they update.
    """

    # ---- Identity ----
    run_id: str
    run_type: str  # "market_open" | "market_close"

    # ---- Routing ----
    next_specialist: str  # Name of the next specialist to invoke, or "done"
    completed_stages: list[str]  # Names of specialists that have finished

    # ---- Data stage outputs ----
    snapshot_id: str
    portfolio: dict[str, Any]  # Raw snapshot dict from DB
    instrument_ids: list[int]  # eToro instrument IDs from portfolio
    instrument_map: dict[int, Any]  # etoro_id → Instrument metadata
    candle_counts: dict[int, int]  # etoro_id → number of candles inserted

    # ---- Analysis stage outputs ----
    analyses_created: int

    # ---- News stage outputs ----
    news_context: list[dict[str, Any]] | None  # List of headline dicts or None

    # ---- Commentary stage outputs ----
    commentary: dict[str, Any] | None  # Parsed commentary dict or None

    # ---- Report stage outputs ----
    report: Any  # Report object from generate_report()
    report_path: str | None

    # ---- Lifecycle ----
    errors: list[dict[str, Any]]
    start_time: float  # time.perf_counter() value
    duration_ms: int
