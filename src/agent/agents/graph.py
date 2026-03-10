"""LangGraph pipeline graph builder and orchestrator routing node.

Builds a ``StateGraph`` where an LLM-powered orchestrator node routes
between specialist agent nodes.  After each specialist runs, control
returns to the orchestrator which decides the next step.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from agent.agents.base import AgentContext, BaseSpecialist
from agent.agents.state import PipelineState

logger = structlog.get_logger(__name__)

# Max iterations to prevent infinite loops
_MAX_ITERATIONS = 15

# Specialists that run procedurally (no ReAct agent loop).
# Currently all specialists are procedural -- the LLM is only used for
# orchestrator routing decisions.  Individual specialists can be removed
# from this set later to enable ReAct-based execution.
_PROCEDURAL_SPECIALISTS = {"data", "analysis", "news", "commentary", "report"}


# =====================================================================
# Routing model (structured output from the orchestrator LLM)
# =====================================================================


class RoutingDecision(BaseModel):
    """The orchestrator LLM's decision on what to do next."""

    next_specialist: str = Field(
        description=(
            "Name of the next specialist to invoke, or 'done' if "
            "the pipeline is complete."
        )
    )
    reasoning: str = Field(
        description="Brief explanation of why this specialist was chosen."
    )


# =====================================================================
# Orchestrator system prompt
# =====================================================================


def _build_orchestrator_prompt(specialists: list[BaseSpecialist]) -> str:
    """Build the system prompt for the orchestrator routing LLM."""
    specialist_descriptions = "\n".join(
        f"- **{s.name}**: {s.description}" for s in specialists
    )

    return (
        "You are the orchestrator for a trading portfolio analysis pipeline. "
        "Your job is to route work to specialist agents in the right order.\n\n"
        "## Available Specialists\n"
        f"{specialist_descriptions}\n\n"
        "## Pipeline Rules\n"
        "1. Always start with 'data' to fetch portfolio and market data\n"
        "2. Run 'analysis' after data collection is complete\n"
        "3. Run 'news' after analysis to fetch world news context\n"
        "4. Run 'commentary' after news and analysis are complete\n"
        "5. Run 'report' last to assemble and display the final report\n"
        "6. Respond with 'done' when all stages are complete\n\n"
        "## Adaptive Behavior\n"
        "- If analysis results indicate weak data, you may route back to "
        "'data' to fetch additional candles before proceeding\n"
        "- If a critical stage fails, you may retry it once or skip to "
        "the next stage\n"
        "- Never call a specialist that has already completed successfully "
        "unless you have a specific reason to re-run it\n\n"
        "## Decision Format\n"
        "Choose the next specialist by name, or 'done' to finish.\n"
        "Provide brief reasoning for your decision."
    )


# =====================================================================
# Fallback routing (deterministic, no LLM)
# =====================================================================

_FALLBACK_ORDER = ["data", "analysis", "news", "commentary", "report"]


def _fallback_next(completed: list[str], available: set[str]) -> str:
    """Deterministic fallback: pick the first uncompleted stage."""
    for stage in _FALLBACK_ORDER:
        if stage not in completed and stage in available:
            return stage
    return "done"


# =====================================================================
# Node factories
# =====================================================================


def _create_orchestrator_node(
    model: ChatGoogleGenerativeAI | None,
    specialists: list[BaseSpecialist],
) -> Any:
    """Create the orchestrator node function.

    The node summarises current pipeline state, calls the LLM with
    structured output to get a ``RoutingDecision``, and writes the
    ``next_specialist`` back into the state.

    When *model* is ``None``, deterministic fallback routing is used.
    """
    system_prompt = _build_orchestrator_prompt(specialists)
    specialist_names = {s.name for s in specialists}

    def orchestrator_node(state: PipelineState) -> dict[str, Any]:
        completed = state.get("completed_stages", [])
        errors = state.get("errors", [])
        iteration = len(completed)

        # Guard against infinite loops
        if iteration >= _MAX_ITERATIONS:
            logger.warning("max_iterations_reached", completed=completed)
            return {"next_specialist": "done"}

        # Deterministic fallback when no model is available
        if model is None:
            next_name = _fallback_next(completed, specialist_names)
            logger.info(
                "orchestrator_fallback_routing",
                next_specialist=next_name,
            )
            return {"next_specialist": next_name}

        # Guard against infinite loops
        if iteration >= _MAX_ITERATIONS:
            logger.warning("max_iterations_reached", completed=completed)
            return {"next_specialist": "done"}

        # Build a concise status summary for the LLM
        status_lines = [
            f"Run ID: {state.get('run_id', '?')}",
            f"Run type: {state.get('run_type', '?')}",
            f"Completed stages: {completed or 'none'}",
            f"Instruments: {len(state.get('instrument_ids', []))}",
            f"Candle counts: {state.get('candle_counts', {})}",
            f"Analyses created: {state.get('analyses_created', 0)}",
            f"News context: {'yes' if state.get('news_context') else 'no'}",
            f"Commentary: {'yes' if state.get('commentary') else 'no'}",
            f"Report: {'yes' if state.get('report') else 'no'}",
            f"Errors: {len(errors)}",
        ]
        status = "\n".join(status_lines)

        try:
            structured_model = model.with_structured_output(RoutingDecision)
            decision: RoutingDecision = structured_model.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Current pipeline state:\n{status}\n\nWhat should we do next?"),
            ])

            next_name = decision.next_specialist.strip().lower()

            # Validate the decision
            if next_name != "done" and next_name not in specialist_names:
                logger.warning(
                    "invalid_routing_decision",
                    decision=next_name,
                    available=list(specialist_names),
                )
                next_name = _fallback_next(completed, specialist_names)

            logger.info(
                "orchestrator_routing",
                next_specialist=next_name,
                reasoning=decision.reasoning,
                completed=completed,
            )

            return {"next_specialist": next_name}

        except Exception as exc:
            logger.warning(
                "orchestrator_llm_failed",
                error=str(exc),
            )
            # Fall back to deterministic order
            next_name = _fallback_next(completed, specialist_names)
            logger.info(
                "orchestrator_fallback_routing",
                next_specialist=next_name,
            )
            return {"next_specialist": next_name}

    return orchestrator_node


def _create_specialist_node(
    specialist: BaseSpecialist,
    model: ChatGoogleGenerativeAI | None,
    ctx: AgentContext,
) -> Any:
    """Create a graph node that runs a specialist.

    When *model* is ``None`` or the specialist is procedural, the
    specialist's ``run_procedural`` method is called directly.
    Otherwise a ReAct agent loop handles tool calling.
    """

    def specialist_node(state: PipelineState) -> dict[str, Any]:
        logger.info(
            "specialist_start",
            specialist=specialist.name,
            run_id=state.get("run_id", "?"),
        )

        # Pass commentary data to report specialist
        if specialist.name == "report":
            specialist._commentary_dict = state.get("commentary")

        # Pass news context to commentary specialist
        if specialist.name == "commentary":
            specialist._news_context = state.get("news_context")

        if model is None or specialist.name in _PROCEDURAL_SPECIALISTS:
            # Procedural: call specialist's run_procedural method
            specialist.run_procedural(state, ctx)
        else:
            # LLM-powered: use ReAct agent
            tools = specialist.create_tools(ctx)
            _run_react_agent(specialist, tools, state, ctx, model)

        # Post-processing: read DB state and update pipeline state
        updates = specialist.process_results(state, ctx)
        completed = list(state.get("completed_stages", []))
        if specialist.name not in completed:
            completed.append(specialist.name)
        updates["completed_stages"] = completed

        logger.info(
            "specialist_complete",
            specialist=specialist.name,
            updates=list(updates.keys()),
        )
        return updates

    return specialist_node


def _run_react_agent(
    specialist: BaseSpecialist,
    tools: list[Any],
    state: PipelineState,
    ctx: AgentContext,
    model: ChatGoogleGenerativeAI,
) -> None:
    """Run a ReAct agent loop for an LLM-powered specialist."""
    system_prompt = specialist.get_system_prompt()

    # Build a context message with current state info
    context_parts = [f"Run ID: {ctx.run_id}", f"Run type: {ctx.run_type}"]

    if specialist.name == "data":
        context_parts.append("Fetch the portfolio, resolve instruments, and download candles.")
    elif specialist.name == "analysis":
        instrument_ids = state.get("instrument_ids", [])
        instrument_map = state.get("instrument_map", {})
        context_parts.append(f"Instrument IDs to analyse: {instrument_ids}")
        # Build metadata string for sector analysis
        meta_parts = []
        for iid in instrument_ids:
            inst = instrument_map.get(iid, {})
            symbol = inst.get("symbol", f"ID:{iid}")
            exchange = inst.get("exchange_id", inst.get("exchange", ""))
            meta_parts.append(f"{iid}:{symbol}:{exchange}")
        if meta_parts:
            context_parts.append(f"Instrument metadata: {' | '.join(meta_parts)}")
    elif specialist.name == "commentary":
        snapshot_id = state.get("snapshot_id", "")
        context_parts.append(f"Snapshot ID: {snapshot_id}")
        context_parts.append(
            f"Analyses created: {state.get('analyses_created', 0)}"
        )

    context_message = "\n".join(context_parts)

    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=system_prompt,
    )

    try:
        agent.invoke(
            {"messages": [HumanMessage(content=context_message)]},
        )
    except Exception as exc:
        logger.error(
            "react_agent_failed",
            specialist=specialist.name,
            error=str(exc),
        )
        # errors are collected by the specialist's tools; we don't re-raise


# =====================================================================
# Graph builder
# =====================================================================


def _route_to_specialist(state: PipelineState) -> str:
    """Conditional edge: read next_specialist from state."""
    return state.get("next_specialist", "done")


def build_pipeline_graph(
    specialists: list[BaseSpecialist],
    model: ChatGoogleGenerativeAI | None,
    ctx: AgentContext,
) -> CompiledStateGraph:
    """Build and compile the LangGraph pipeline.

    Args:
        specialists: Registered specialist instances.
        model: The LLM used for orchestrator routing and specialist agents.
            Pass ``None`` for deterministic (fallback) routing.
        ctx: Shared agent context (DB, client, settings).

    Returns:
        A compiled ``StateGraph`` ready to ``.invoke()``.
    """
    graph = StateGraph(PipelineState)

    # Add the orchestrator node
    orchestrator_fn = _create_orchestrator_node(model, specialists)
    graph.add_node("orchestrator", orchestrator_fn)

    # Add specialist nodes
    route_map: dict[str, str] = {}
    for specialist in specialists:
        node_fn = _create_specialist_node(specialist, model, ctx)
        graph.add_node(specialist.name, node_fn)
        # Each specialist returns to the orchestrator
        graph.add_edge(specialist.name, "orchestrator")
        route_map[specialist.name] = specialist.name

    # "done" maps to the graph END
    route_map["done"] = END

    # Entry: start at orchestrator
    graph.add_edge(START, "orchestrator")

    # Orchestrator routes conditionally
    graph.add_conditional_edges("orchestrator", _route_to_specialist, route_map)

    return graph.compile()
