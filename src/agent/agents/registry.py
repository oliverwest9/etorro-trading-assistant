"""Specialist agent registry.

Provides a simple registry so that new specialists can be added by
creating a class and calling ``register_specialist()``.  The
orchestrator graph reads from this registry to discover available
specialists at graph-build time.
"""

from __future__ import annotations

from agent.agents.base import BaseSpecialist

_REGISTRY: dict[str, BaseSpecialist] = {}


def register_specialist(specialist: BaseSpecialist) -> None:
    """Register a specialist instance by its ``name``."""
    if specialist.name in _REGISTRY:
        raise ValueError(
            f"Specialist {specialist.name!r} is already registered"
        )
    _REGISTRY[specialist.name] = specialist


def get_specialist(name: str) -> BaseSpecialist:
    """Retrieve a registered specialist by name.

    Raises:
        KeyError: If no specialist with that name is registered.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"No specialist registered with name {name!r}. "
            f"Available: {list(_REGISTRY)}"
        ) from None


def get_all_specialists() -> list[BaseSpecialist]:
    """Return all registered specialists in registration order."""
    return list(_REGISTRY.values())


def clear_registry() -> None:
    """Remove all registered specialists (used in tests)."""
    _REGISTRY.clear()
