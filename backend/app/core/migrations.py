"""Programmatic Alembic runner.

This — together with the ``alembic`` CLI itself — is the *only* mechanism
allowed to create or alter the database schema. Application code must never
use ``Base.metadata.create_all()`` or hand-written DDL (see backend/README.md).
"""

from __future__ import annotations

from alembic.config import Config

from alembic import command
from app.core.config import BACKEND_DIR, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
_ALEMBIC_SCRIPT_LOCATION = BACKEND_DIR / "alembic"


def _alembic_config() -> Config:
    # Absolute paths so this behaves identically no matter the process's
    # current working directory - the same class of bug fixed for the sqlite
    # database path itself (see app.core.db_path.anchor_sqlite_url).
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def run_migrations() -> None:
    """Apply all pending migrations (equivalent to ``alembic upgrade head``)."""
    logger.info("Applying database migrations -> %s", settings.database_url)
    command.upgrade(_alembic_config(), "head")
