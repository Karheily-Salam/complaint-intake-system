"""Drop and recreate all tables from the ORM metadata.

Convenience for the prototype only. The migration path is
``alembic upgrade head``; use this when you just want a clean SQLite file fast.

    python -m scripts.reset_db
"""

from __future__ import annotations

from app.core.database import engine
from app.db.base import Base


def main() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("Recreated all tables in", engine.url)


if __name__ == "__main__":
    main()
