"""Reset the local prototype SQLite database via Alembic.

Deletes the current SQLite file (if any) and re-applies every migration from
scratch, so the result is guaranteed to match ``alembic upgrade head`` exactly.
This never uses ``Base.metadata`` or ad-hoc DDL - Alembic is the only
schema-authoring mechanism.

    python -m scripts.reset_db
"""

from __future__ import annotations

from app.core.config import settings
from app.core.db_path import sqlite_file_path
from app.core.migrations import run_migrations


def main() -> None:
    db_path = sqlite_file_path(settings.database_url)
    if db_path is not None and db_path.exists():
        db_path.unlink()
        print("Removed", db_path)
    run_migrations()
    print("Database re-created at head via Alembic:", settings.database_url)


if __name__ == "__main__":
    main()
