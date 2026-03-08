"""Shared type definitions for the agent module.

This module contains common type aliases used across the agent codebase
to ensure consistency and maintainability.
"""

from __future__ import annotations

from typing import Literal

# Valid run types for the agent pipeline
RunType = Literal["market_open", "market_close"]
