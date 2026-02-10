"""Initialise the SurrealDB schema.

Usage::

    python scripts/init_db.py

Connects to the database specified by the ``SURREAL_*`` env vars (or ``.env``
file), applies the full schema from ``db/schema.py``, and reports the result.
Running this script a second time is safe — the schema is idempotent.
"""

from agent.config import Settings
from agent.db.connection import get_connection
from agent.db.schema import apply_schema


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # populated from env/.env

    print("=" * 60)
    print("SurrealDB Schema Initialisation")
    print("=" * 60)
    print(f"  URL:       {settings.surreal_url}")
    print(f"  Namespace: {settings.surreal_namespace}")
    print(f"  Database:  {settings.surreal_database}")
    print()

    with get_connection(settings) as db:
        apply_schema(db)

        # Quick verification — query table list
        result = db.query("INFO FOR DB;")
        # INFO FOR DB returns a dict with a 'tables' key (among others)
        if isinstance(result, dict) and "tables" in result:
            tables = result["tables"]
            if isinstance(tables, dict):
                print(f"  Tables created: {len(tables)}")
                for name in sorted(tables.keys()):
                    print(f"    - {name}")
            else:
                print(f"  Tables: {tables}")
        else:
            print(f"  Raw result: {result}")

    print()
    print("Schema applied successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
