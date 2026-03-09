"""LLM commentary generation for the eToro trading agent.

This module assembles portfolio and analysis data into a structured
prompt, sends it to Google Gemini, and parses the structured JSON
response into typed models.

The workflow has three layers that can be used independently:

1. **build_commentary_request()** — pure function, assembles data
2. **format_prompt()** — pure function, renders prompt string
3. **generate_commentary()** — calls Gemini API and parses response

Layers 1–2 require no API key and can be used to inspect payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel, Field

from agent.config import Settings

logger = structlog.get_logger(__name__)


# =====================================================================
# Response models (what Gemini returns)
# =====================================================================


class PositionCommentary(BaseModel):
    """LLM-generated commentary for a single portfolio position."""

    instrument_id: int
    symbol: str
    commentary: str


class Recommendation(BaseModel):
    """A single actionable recommendation from the LLM."""

    instrument_id: int
    symbol: str
    action: str = Field(description="One of: buy, sell, hold, reduce, increase")
    conviction: str = Field(description="One of: high, medium, low")
    reasoning: str


class CommentaryResponse(BaseModel):
    """Structured response from the LLM containing all commentary."""

    summary: str = Field(description="One-line headline summary of market conditions")
    market_context: str = Field(
        description="Broader market commentary paragraph"
    )
    position_commentaries: list[PositionCommentary] = Field(
        description="Per-position assessment"
    )
    recommendations: list[Recommendation] = Field(
        description="Actionable recommendations for each position"
    )


# =====================================================================
# Request model (what we send to the LLM)
# =====================================================================


@dataclass(frozen=True)
class PositionData:
    """Enriched position data ready for the LLM prompt."""

    instrument_id: int
    symbol: str
    name: str
    direction: str
    open_rate: float
    amount: float
    units: float
    pnl: float | None
    trend: str
    trend_strength: float
    support: float | None
    resistance: float | None
    momentum_signal: str
    sector_group: str | None
    sector_avg_return_pct: float | None


@dataclass(frozen=True)
class SectorOverview:
    """Summary of a sector/exchange group for prompt context."""

    group_name: str
    instrument_count: int
    avg_return_pct: float


@dataclass(frozen=True)
class RiskOverview:
    """Per-instrument risk summary for the LLM prompt."""

    symbol: str
    volatility_pct: float
    max_drawdown_pct: float
    risk_adjusted_return: float


@dataclass(frozen=True)
class CritiqueSummary:
    """Portfolio-level critique data for inclusion in the LLM prompt.

    Built from a ``CritiqueResult`` by ``build_commentary_request()``.
    """

    portfolio_return_pct: float
    inflation_target_pct: float
    beats_inflation: bool
    return_vs_inflation_pct: float
    portfolio_volatility_pct: float
    portfolio_sharpe: float
    diversification_rating: str
    herfindahl_index: float
    top_position_symbol: str
    top_position_pct: float
    cash_allocation_pct: float
    risk_overviews: list[RiskOverview] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommentaryRequest:
    """All data needed to generate LLM commentary.

    This is a pure data object assembled by ``build_commentary_request()``.
    It can be serialised to a prompt string via ``format_prompt()``.
    """

    run_type: str
    total_value: float
    cash_available: float
    open_positions_count: int
    total_pnl: float
    positions: list[PositionData] = field(default_factory=list)
    sectors: list[SectorOverview] = field(default_factory=list)
    critique: CritiqueSummary | None = None


# =====================================================================
# Payload construction (pure functions — no API calls)
# =====================================================================


def build_commentary_request(
    *,
    run_type: str,
    snapshot: dict[str, Any],
    analyses: list[dict[str, Any]],
    instrument_map: dict[int, dict[str, Any]],
    critique: Any | None = None,
) -> CommentaryRequest:
    """Assemble a ``CommentaryRequest`` from pipeline data.

    All arguments are plain dicts as stored in / returned from SurrealDB,
    not Pydantic models — this keeps the function decoupled from the
    eToro client layer.

    Args:
        run_type: ``"market_open"`` or ``"market_close"``.
        snapshot: Portfolio snapshot dict from SurrealDB
            (keys: ``total_value``, ``cash_available``, ``open_positions``,
            ``total_pnl``, ``positions``).
        analyses: List of analysis dicts from SurrealDB
            (keys: ``instrument``, ``trend``, ``trend_strength``,
            ``price_action``, ``sector_context``).
        instrument_map: Mapping of eToro instrument ID → instrument dict
            (keys: ``etoro_id``, ``symbol``, ``name``).
        critique: Optional ``CritiqueResult`` from the financial analyst
            critic.  When provided, its data is summarised and included
            in the prompt to the LLM.

    Returns:
        A fully populated ``CommentaryRequest``.
    """
    # Index analyses by instrument eToro ID for fast lookup
    analysis_by_id: dict[int, dict[str, Any]] = {}
    for a in analyses:
        # The instrument field may be a record ID string like "instrument:xyz"
        # or have an etoro_id. We need to match via etoro_id.
        etoro_id = _extract_etoro_id(a)
        if etoro_id is not None:
            analysis_by_id[etoro_id] = a

    # Build per-position data
    positions: list[PositionData] = []
    for pos in snapshot.get("positions", []):
        iid = pos.get("instrument_id")
        if iid is None:
            continue

        inst = instrument_map.get(iid, {})
        analysis = analysis_by_id.get(iid, {})
        pa = analysis.get("price_action", {})
        sc = analysis.get("sector_context")

        positions.append(
            PositionData(
                instrument_id=iid,
                symbol=inst.get("symbol", f"ID:{iid}"),
                name=inst.get("name", "Unknown"),
                direction="Long" if pos.get("is_buy", True) else "Short",
                open_rate=pos.get("open_rate", 0.0),
                amount=pos.get("amount", 0.0),
                units=pos.get("units", 0.0),
                pnl=_extract_pnl(pos),
                trend=analysis.get("trend", "unknown"),
                trend_strength=analysis.get("trend_strength", 0.0),
                support=pa.get("support"),
                resistance=pa.get("resistance"),
                momentum_signal=pa.get("momentum_signal", "unknown"),
                sector_group=sc.get("group_name") if sc else None,
                sector_avg_return_pct=sc.get("avg_return_pct") if sc else None,
            )
        )

    # Build sector overview from unique sector contexts
    seen_groups: dict[str, SectorOverview] = {}
    for a in analyses:
        sc = a.get("sector_context")
        if sc and sc.get("group_name") not in seen_groups:
            seen_groups[sc["group_name"]] = SectorOverview(
                group_name=sc["group_name"],
                instrument_count=sc.get("instrument_count", 0),
                avg_return_pct=sc.get("avg_return_pct", 0.0),
            )

    # Build critique summary (if a CritiqueResult was provided)
    critique_summary = _build_critique_summary(critique) if critique else None

    return CommentaryRequest(
        run_type=run_type,
        total_value=snapshot.get("total_value", 0.0),
        cash_available=snapshot.get("cash_available", 0.0),
        open_positions_count=snapshot.get("open_positions", 0),
        total_pnl=snapshot.get("total_pnl", 0.0),
        positions=positions,
        sectors=sorted(seen_groups.values(), key=lambda s: s.group_name),
        critique=critique_summary,
    )


def _extract_etoro_id(analysis: dict[str, Any]) -> int | None:
    """Extract the eToro instrument ID from an analysis dict.

    The ``instrument`` field may be stored as:
    - An int directly (``etoro_id`` key on the analysis)
    - A SurrealDB record ID string like ``"instrument:abc"``
    - A dict with an ``etoro_id`` key

    We also check for a top-level ``instrument_etoro_id`` key set
    during persistence.
    """
    # Direct key (set by db/analysis.py on creation)
    if "instrument_etoro_id" in analysis:
        val = analysis["instrument_etoro_id"]
        if isinstance(val, int):
            return val

    # Nested instrument dict
    inst = analysis.get("instrument")
    if isinstance(inst, dict):
        return inst.get("etoro_id")

    return None


def _extract_pnl(position: dict[str, Any]) -> float | None:
    """Extract P&L from a position dict.

    Handles both flattened (``pnl`` key) and nested
    (``unrealized_pnl.pnl``) structures.
    """
    if "pnl" in position:
        return position["pnl"]
    upnl = position.get("unrealized_pnl")
    if isinstance(upnl, dict):
        return upnl.get("pnl")
    return None


def _build_critique_summary(critique: Any) -> CritiqueSummary:
    """Convert a ``CritiqueResult`` into a ``CritiqueSummary`` for the prompt."""
    risk_overviews = [
        RiskOverview(
            symbol=rm.symbol,
            volatility_pct=rm.daily_volatility_pct,
            max_drawdown_pct=rm.max_drawdown_pct,
            risk_adjusted_return=rm.risk_adjusted_return,
        )
        for rm in getattr(critique, "risk_metrics", [])
    ]
    div = getattr(critique, "diversification", None)
    return CritiqueSummary(
        portfolio_return_pct=getattr(critique, "portfolio_return_pct", 0.0),
        inflation_target_pct=getattr(critique, "inflation_target_pct", 0.0),
        beats_inflation=getattr(critique, "beats_inflation", False),
        return_vs_inflation_pct=getattr(critique, "return_vs_inflation_pct", 0.0),
        portfolio_volatility_pct=getattr(critique, "portfolio_volatility_pct", 0.0),
        portfolio_sharpe=getattr(critique, "portfolio_sharpe", 0.0),
        diversification_rating=div.rating if div else "unknown",
        herfindahl_index=div.herfindahl_index if div else 0.0,
        top_position_symbol=div.top_position_symbol if div else "N/A",
        top_position_pct=div.top_position_pct if div else 0.0,
        cash_allocation_pct=getattr(critique, "cash_allocation_pct", 0.0),
        risk_overviews=risk_overviews,
        suggestions=list(getattr(critique, "suggestions", [])),
    )


# =====================================================================
# Prompt formatting (pure function)
# =====================================================================

SYSTEM_PROMPT = """\
You are a senior financial analyst and portfolio critic advising on an \
eToro portfolio. Your primary objective is to help the investor beat \
inflation over the long term through responsible, evidence-based decisions. \
You have been given the current portfolio state, technical analysis data \
for each position, and a quantitative portfolio critique with risk metrics.

Your job is to:

1. Provide a one-line summary headline of current market conditions.
2. Write a market context paragraph covering broader trends relevant \
to the positions held.
3. For each position, write a brief commentary assessing its current \
state based on the technical data and risk metrics provided.
4. For each position, provide a specific recommendation (buy, sell, \
hold, reduce, or increase) with a conviction level (high, medium, low) \
and clear reasoning referencing the technical and risk data.

Financial analyst principles — always apply these:
- **Long-term focus**: prioritise sustainable growth over short-term \
gains. The goal is to outpace inflation, not to chase momentum.
- **Risk awareness**: factor in volatility, maximum drawdown, and \
risk-adjusted returns when making recommendations. High returns are \
meaningless if they come with portfolio-destroying risk.
- **Diversification**: flag concentration risk. A well-diversified \
portfolio across sectors and geographies is more resilient.
- **Capital preservation**: avoid recommending aggressive buys when \
drawdowns are severe or volatility is extreme.
- **Inflation benchmark**: explicitly compare portfolio performance \
against the inflation target. If the portfolio is underperforming \
inflation, recommend corrective action.
- **Position sizing**: flag any position that dominates the portfolio. \
No single holding should risk the overall strategy.
- **Cash management**: too much cash is a drag on returns; too little \
leaves no dry powder for opportunities.
- **Evidence-based**: reference actual numbers — price levels, \
support/resistance, volatility percentages, Sharpe ratios, drawdowns.

Additional guidelines:
- Be specific — reference actual price levels, support/resistance, \
and trend data from the analysis.
- This is advisory only — you are not executing trades.
- Consider the portfolio as a whole — diversification, concentration \
risk, and sector exposure matter.
- For market_open runs, focus on overnight moves and the day ahead.
- For market_close runs, focus on the session's performance and \
overnight considerations.
- Keep commentary concise but actionable.

Respond with valid JSON matching the provided schema.\
"""


def format_prompt(request: CommentaryRequest) -> str:
    """Render a ``CommentaryRequest`` into the full user-message prompt.

    The returned string is what gets sent as the user message to the
    LLM (alongside the system prompt).  It is deterministic and can be
    inspected without making any API calls.
    """
    lines: list[str] = []

    # Header
    run_label = request.run_type.replace("_", " ").title()
    lines.append(f"## Run Type: {run_label}")
    lines.append("")

    # Portfolio overview
    lines.append("## Portfolio Overview")
    lines.append(f"- Total value: ${request.total_value:,.2f}")
    lines.append(f"- Cash available: ${request.cash_available:,.2f}")
    lines.append(f"- Open positions: {request.open_positions_count}")
    lines.append(f"- Total P&L: ${request.total_pnl:,.2f}")
    lines.append("")

    # Sector overview
    if request.sectors:
        lines.append("## Sector / Exchange Performance")
        for s in request.sectors:
            lines.append(
                f"- **{s.group_name}**: {s.instrument_count} instruments, "
                f"avg return {s.avg_return_pct:+.2f}%"
            )
        lines.append("")

    # Per-position data
    lines.append("## Positions")
    lines.append("")

    if not request.positions:
        lines.append("No open positions.")
    else:
        for i, p in enumerate(request.positions, 1):
            lines.append(f"### {i}. {p.symbol} — {p.name}")
            lines.append(f"- Direction: {p.direction}")
            lines.append(f"- Open rate: ${p.open_rate:,.4f}")
            lines.append(f"- Amount invested: ${p.amount:,.2f}")
            lines.append(f"- Units: {p.units:.4f}")
            if p.pnl is not None:
                lines.append(f"- Unrealised P&L: ${p.pnl:,.2f}")
            lines.append(f"- Trend: {p.trend} (strength: {p.trend_strength:.2f})")
            if p.support is not None:
                lines.append(f"- Support: ${p.support:,.4f}")
            if p.resistance is not None:
                lines.append(f"- Resistance: ${p.resistance:,.4f}")
            lines.append(f"- Momentum: {p.momentum_signal}")
            if p.sector_group:
                sector_line = f"- Sector: {p.sector_group}"
                if p.sector_avg_return_pct is not None:
                    sector_line += f" (group avg return: {p.sector_avg_return_pct:+.2f}%)"
                lines.append(sector_line)
            lines.append("")

    # Portfolio critique (financial analyst assessment)
    if request.critique:
        c = request.critique
        lines.append("## Financial Analyst Critique")
        lines.append("")
        lines.append(f"- Portfolio return: {c.portfolio_return_pct:+.2f}%")
        lines.append(f"- Inflation target: {c.inflation_target_pct:.1f}%")
        status = "BEATING" if c.beats_inflation else "BELOW"
        lines.append(
            f"- Inflation status: **{status}** "
            f"(excess: {c.return_vs_inflation_pct:+.2f}pp)"
        )
        lines.append(f"- Portfolio volatility (annualised): {c.portfolio_volatility_pct:.2f}%")
        lines.append(f"- Portfolio Sharpe ratio: {c.portfolio_sharpe:.2f}")
        lines.append(f"- Cash allocation: {c.cash_allocation_pct:.1f}%")
        lines.append(
            f"- Diversification: {c.diversification_rating} "
            f"(HHI: {c.herfindahl_index:.2f}, top position: "
            f"{c.top_position_symbol} at {c.top_position_pct:.1f}%)"
        )
        lines.append("")

        if c.risk_overviews:
            lines.append("### Per-Position Risk")
            for ro in c.risk_overviews:
                lines.append(
                    f"- **{ro.symbol}**: vol {ro.volatility_pct:.1f}%, "
                    f"max DD {ro.max_drawdown_pct:.1f}%, "
                    f"risk-adj return {ro.risk_adjusted_return:.2f}"
                )
            lines.append("")

        if c.suggestions:
            lines.append("### Analyst Suggestions")
            for idx, s in enumerate(c.suggestions, 1):
                lines.append(f"{idx}. {s}")
            lines.append("")

    return "\n".join(lines)


# =====================================================================
# Gemini API integration
# =====================================================================


def generate_commentary(
    request: CommentaryRequest,
    settings: Settings,
) -> CommentaryResponse:
    """Send the commentary request to Google Gemini and parse the response.

    Uses Gemini's structured output (``response_mime_type="application/json"``)
    to get a typed JSON response matching ``CommentaryResponse``.

    Args:
        request: The assembled commentary request.
        settings: Application settings (provides API key and model name).

    Returns:
        Parsed ``CommentaryResponse``.

    Raises:
        RuntimeError: If the Gemini API call fails or returns unparseable output.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.llm_api_key)

    prompt = format_prompt(request)

    logger.info(
        "llm_request",
        model=settings.llm_model,
        positions=len(request.positions),
    )

    try:
        response = client.models.generate_content(
            model=settings.llm_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=CommentaryResponse,
            ),
        )
        raw_text = response.text
    except Exception as exc:
        logger.error("llm_api_error", error=str(exc))
        raise RuntimeError(f"Gemini API call failed: {exc}") from exc

    logger.debug("llm_raw_response", text=raw_text[:500])

    try:
        return CommentaryResponse.model_validate_json(raw_text)
    except Exception as exc:
        logger.error(
            "llm_parse_error",
            error=str(exc),
            raw_text=raw_text[:1000],
        )
        raise RuntimeError(
            f"Failed to parse Gemini response as CommentaryResponse: {exc}"
        ) from exc
