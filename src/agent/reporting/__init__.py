"""Public API for the reporting package.

Exposes the report model, generator, and formatters.
"""

from agent.reporting.formatter import format_markdown, format_terminal
from agent.reporting.generator import (
    RecommendationChange,
    Report,
    ReportDiff,
    generate_report,
)

__all__ = [
    "RecommendationChange",
    "Report",
    "ReportDiff",
    "format_markdown",
    "format_terminal",
    "generate_report",
]
