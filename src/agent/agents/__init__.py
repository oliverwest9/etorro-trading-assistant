"""Multi-agent pipeline using LangGraph.

This package provides a LangGraph-based orchestrator that routes between
specialist agents (data, analysis, commentary, report) to execute the
trading agent pipeline.
"""

from agent.agents.base import AgentContext, BaseSpecialist
from agent.agents.graph import build_pipeline_graph
from agent.agents.registry import get_all_specialists, get_specialist, register_specialist
from agent.agents.state import PipelineState

__all__ = [
    "AgentContext",
    "BaseSpecialist",
    "PipelineState",
    "build_pipeline_graph",
    "get_all_specialists",
    "get_specialist",
    "register_specialist",
]
