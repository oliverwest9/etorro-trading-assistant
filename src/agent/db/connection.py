"""SurrealDB connection factory.

Supports three URL modes via the ``SURREAL_URL`` setting:

- ``ws://host:port/rpc`` — remote WebSocket (local Docker dev)
- ``memory://`` — in-memory embedded (tests, ephemeral use)
- ``file:///path/to/db`` — embedded file-based (future AWS deployment)

The bare string ``memory`` is accepted for convenience and normalised to
``memory://`` so that the SDK's URL parser can extract the scheme correctly.

Usage::

    from agent.config import Settings
    from agent.db.connection import get_connection

    settings = Settings()
    with get_connection(settings) as db:
        result = db.query("INFO FOR DB;")
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import structlog
from surrealdb import Surreal
from surrealdb.connections.sync_template import SyncTemplate

from agent.config import Settings

logger = structlog.get_logger(__name__)

# Bare strings that need a ``://`` suffix so the SDK's URL parser can
# extract a valid scheme via ``urlparse()``.
_BARE_SCHEME_ALIASES: dict[str, str] = {
    "memory": "memory://",
    "mem": "mem://",
}


def _normalise_url(url: str) -> str:
    """Ensure *url* has a proper scheme the SDK can parse."""
    return _BARE_SCHEME_ALIASES.get(url, url)


def _is_embedded(url: str) -> bool:
    """Return *True* when the URL points to an embedded (non-remote) engine.

    Accepts both bare aliases (``memory``) and full URIs (``memory://``).
    """
    normalised = _normalise_url(url)
    return normalised in ("memory://", "mem://") or normalised.startswith(
        ("file://", "surrealkv://")
    )


@contextmanager
def get_connection(settings: Settings) -> Generator[SyncTemplate, None, None]:
    """Open a SurrealDB connection, authenticate and select the namespace/database.

    The caller receives a ready-to-use ``Surreal`` instance.  The connection
    is closed automatically when the context manager exits.

    Args:
        settings: Application settings containing connection details.

    Yields:
        A connected and authenticated ``Surreal`` database handle.
    """
    raw_url = settings.surreal_url
    url = _normalise_url(raw_url)
    embedded = _is_embedded(url)

    logger.info(
        "db_connecting",
        url=url,
        namespace=settings.surreal_namespace,
        database=settings.surreal_database,
        embedded=embedded,
    )

    with Surreal(url) as db:
        if not embedded:
            db.signin(
                {
                    "username": settings.surreal_user,
                    "password": settings.surreal_pass,
                }
            )
        db.use(settings.surreal_namespace, settings.surreal_database)
        logger.info("db_connected")
        yield db
