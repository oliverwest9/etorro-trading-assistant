"""Sector / exchange-group analysis.

Groups instruments by their eToro ``exchange_id`` into market regions
(US, UK, EU, Crypto, Other) and computes a simple average return per
group.  This gives the agent a cross-market perspective to accompany
per-instrument price-action analysis.

This module is a **pure function** — no API or DB calls.
"""

from __future__ import annotations

from typing import Any

from agent.analysis.types import SectorGroupResult, SectorResult


# Exchange-ID → group mapping (from eToro's internal exchange IDs)
EXCHANGE_GROUPS: dict[str, str] = {
    "5": "US",     # NASDAQ-like
    "33": "US",    # NYSE-like
    "7": "UK",     # London
    "38": "EU",    # European exchange
    "8": "Crypto", # Crypto market
}

DEFAULT_GROUP = "Other"


def _compute_simple_return(candles: list[dict[str, Any]]) -> float:
    """Compute simple return from first to last close in a candle list.

    Args:
        candles: OHLCV dicts sorted by timestamp ascending.

    Returns:
        Percentage return (e.g. 5.0 for +5 %).  Returns 0.0 if
        insufficient data or zero division.
    """
    if len(candles) < 2:
        return 0.0
    first_close = float(candles[0].get("close", 0))
    last_close = float(candles[-1].get("close", 0))
    if first_close == 0:
        return 0.0
    return ((last_close - first_close) / first_close) * 100.0


def analyse_sector(
    instruments: list[dict[str, Any]],
    candle_map: dict[int, list[dict[str, Any]]],
) -> SectorResult:
    """Group instruments by exchange and compute per-group average returns.

    Args:
        instruments: List of instrument dicts (each must have at least
            ``etoro_id``, ``symbol``, and ``exchange`` keys).
        candle_map: Mapping from ``etoro_id`` → list of OHLCV candle dicts
            (each list sorted by timestamp ascending).

    Returns:
        ``SectorResult`` with per-group summaries.
    """
    # Bucket instruments into groups
    buckets: dict[str, list[tuple[int, str, float]]] = {}

    for inst in instruments:
        etoro_id = int(inst.get("etoro_id", 0))
        symbol = str(inst.get("symbol", "???"))
        exchange = inst.get("exchange")

        group_name = EXCHANGE_GROUPS.get(str(exchange), DEFAULT_GROUP) if exchange else DEFAULT_GROUP

        candles = candle_map.get(etoro_id, [])
        ret = round(_compute_simple_return(candles), 4)

        buckets.setdefault(group_name, []).append((etoro_id, symbol, ret))

    # Build group results
    groups: dict[str, SectorGroupResult] = {}
    for group_name, members in sorted(buckets.items()):
        returns = [r for _, _, r in members]
        avg_return = sum(returns) / len(returns) if returns else 0.0
        groups[group_name] = SectorGroupResult(
            group_name=group_name,
            instrument_count=len(members),
            avg_return_pct=round(avg_return, 4),
            instruments=members,
        )

    # Identify best/worst groups
    best_group: str | None = None
    worst_group: str | None = None
    if groups:
        best_group = max(groups, key=lambda g: groups[g].avg_return_pct)
        worst_group = min(groups, key=lambda g: groups[g].avg_return_pct)

    return SectorResult(
        groups=groups,
        best_group=best_group,
        worst_group=worst_group,
    )
