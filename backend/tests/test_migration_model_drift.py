"""The migrations must actually build the schema the models describe.

Alembic is the only schema-authoring mechanism here, which means a model
changed without a matching revision produces a database that works in tests
(they call create_all) and breaks in production (which runs migrations). That
gap is invisible until deploy, so it is worth a test.

The check: migrate an empty database to head, then ask Alembic to autogenerate
against the models. Any operation it wants to perform is drift.
"""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from alembic import command
from app.db.base import Base
from tests.conftest import BACKEND_DIR

# Autogenerate reports these for reasons that are not drift: SQLite renders
# some server defaults differently from how they were declared, and Alembic
# cannot always match an index it did not create in this session.
IGNORED_OPERATION_PREFIXES = ("add_index", "remove_index")


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def describe(diff) -> str:
    """Render one autogenerate diff entry readably."""
    if isinstance(diff, tuple):
        return " ".join(str(part) for part in diff[:3])
    return str(diff)


@pytest.fixture()
def migrated_database(tmp_path, monkeypatch):
    """A fresh database brought to head by the real migrations."""
    db_path = tmp_path / "drift_check.db"
    url = f"sqlite:///{db_path.as_posix()}"

    # alembic/env.py reads the URL from settings, so point it at this file.
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "database_url", url)
    command.upgrade(alembic_config(url), "head")
    return url


def test_migrations_produce_the_schema_the_models_describe(migrated_database):
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diffs = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    significant = [
        d
        for d in diffs
        if not (isinstance(d, tuple) and str(d[0]).startswith(IGNORED_OPERATION_PREFIXES))
    ]

    assert not significant, (
        "The models and the migrations disagree. Alembic would generate:\n  "
        + "\n  ".join(describe(d) for d in significant)
        + "\n\nRun `alembic revision --autogenerate -m '...'` and review the result: "
        "a model changed without a migration works in tests (which use "
        "create_all) and fails in production (which runs migrations)."
    )


def test_every_model_table_exists_after_migrating(migrated_database):
    """A blunter check that fails with a clearer message than a diff dump."""
    from sqlalchemy import inspect

    engine = create_engine(migrated_database)
    try:
        present = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    expected = set(Base.metadata.tables)
    missing = expected - present

    assert not missing, f"migrations did not create: {', '.join(sorted(missing))}"
