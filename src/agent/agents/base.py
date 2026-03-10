"""Base specialist and agent context definitions.

Every specialist agent extends ``BaseSpecialist`` and implements:

- ``name`` — unique identifier used for graph routing
- ``description`` — one-line description shown to the orchestrator LLM
- ``create_tools(ctx)`` — returns LangChain tools bound to live resources
- ``get_system_prompt()`` — instructions for the specialist's internal LLM
- ``process_results(state, ctx)`` — post-execution hook that reads DB
  state and returns updates to ``PipelineState``
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from surrealdb.connections.sync_template import SyncTemplate

from agent.config import Settings
from agent.etoro.client import EToroClient


@dataclass
class AgentContext:
    """Shared resources available to all specialist agents.

    Passed into ``create_tools()`` so that tool closures can access
    the database, HTTP client, and configuration without globals.
    """

    db: SyncTemplate
    client: EToroClient
    settings: Settings
    run_id: str
    run_type: str
    generate_fn: Any = None


class BaseSpecialist(ABC):
    """Abstract base class for specialist agents.

    Subclass this and implement the abstract methods to create a new
    specialist.  Register it via ``register_specialist()`` and the
    orchestrator will be able to route to it automatically.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this specialist (e.g. ``"data"``)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description shown to the orchestrator LLM."""

    @abstractmethod
    def create_tools(self, ctx: AgentContext) -> list[Any]:
        """Return LangChain tools for this specialist.

        Tools should be closures over ``ctx`` so they have access
        to the DB connection and eToro client.
        """

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this specialist's internal LLM."""

    @abstractmethod
    def process_results(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> dict[str, Any]:
        """Post-execution hook returning ``PipelineState`` updates.

        Called after the specialist's ReAct agent finishes.  Should
        read from SurrealDB (via ``ctx.db``) and return a dict of
        state keys to update.
        """

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Execute this specialist's work procedurally (no LLM reasoning).

        Override in subclasses to provide a deterministic execution path.
        Called when the graph runs without an LLM model.
        """
        raise NotImplementedError(
            f"Specialist '{self.name}' has no procedural implementation"
        )
