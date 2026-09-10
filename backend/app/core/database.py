"""Database engine and session management (synchronous SQLAlchemy 2.0)."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

# `timeout` is how long the driver waits for a lock before raising
# "database is locked"; SQLAlchemy passes it through to sqlite3.connect.
_connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}


def apply_sqlite_pragmas(target: Engine) -> Engine:
    """Configure SQLite to behave sanely under concurrent access.

    Applied per connection, because PRAGMAs are connection-scoped.
    ``journal_mode`` is the exception - it persists on the database file - but
    setting it each time is harmless and means a freshly created volume is
    correct with no manual step.

    - **WAL**: with the default rollback journal a write blocks readers. This
      deployment has an HTTP API and a background email poller writing to the
      same file, so readers not blocking writers is worth having.
    - **busy_timeout**: wait for a held lock instead of failing immediately
      with "database is locked".
    - **foreign_keys**: SQLite ignores foreign keys unless asked, which makes
      the constraints the migrations declare merely decorative otherwise.

    ``synchronous`` is deliberately left at its default (FULL): this system
    writes a handful of rows per email, so there is nothing to gain by
    trading durability for write throughput.
    """
    if target.dialect.name != "sqlite":
        return target

    @event.listens_for(target, "connect")
    def _set_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return target


engine = apply_sqlite_pragmas(
    create_engine(
        settings.database_url,
        connect_args=_connect_args,
        echo=settings.db_echo,
        future=True,
    )
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
