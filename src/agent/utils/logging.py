"""Structured logging configuration using ``structlog``.

Call :func:`configure_logging` once at application startup (before any
logger is used) to set the output format (JSON or colored text) and the
minimum log level.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(
    *,
    json: bool = True,
    level: str = "INFO",
) -> None:
    """Configure ``structlog`` and the stdlib ``logging`` root logger.

    Args:
        json: If ``True`` (default), render log entries as single-line
            JSON objects.  If ``False``, use a human-friendly colored
            console renderer.
        level: Minimum log level (``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
            ``"ERROR"``).  Applies to both structlog and stdlib logging.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors run before the final renderer
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)
