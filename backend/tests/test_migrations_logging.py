"""Regression test for a startup hang caused by running Alembic in-process.

Root cause: alembic/env.py called ``logging.config.fileConfig(...)`` with its
default ``disable_existing_loggers=True``. That's harmless for the separate
`alembic` CLI process, but app.core.migrations.run_migrations() runs the same
env.py *inside* the FastAPI/uvicorn process (from the lifespan, so a fresh
clone's database gets migrated automatically). Disabling every logger not
declared in alembic.ini's ``[loggers]`` section silently disabled uvicorn's
own loggers for the rest of the process - so "INFO: Application startup
complete." (and every log line after it, including request logs and any
real startup error) never appeared again, making a perfectly successful
startup look hung.

See test_startup_migration_subprocess.py for the same fix verified against a
real, separately-launched `uvicorn` process.
"""

from __future__ import annotations

import logging

from app.core import migrations as migrations_module


def test_run_migrations_does_not_disable_other_loggers(tmp_path, monkeypatch):
    # An isolated, fresh database for this check - deliberately not the
    # shared test database other tests populate via Base.metadata.create_all
    # (running real Alembic migrations against that would collide with
    # tables that already exist there but were never stamped as migrated).
    db_path = tmp_path / "logging_check.db"
    monkeypatch.setattr(
        migrations_module.settings, "database_url", f"sqlite:///{db_path.as_posix()}"
    )

    # Representative "host application" loggers, configured/enabled before
    # Alembic ever runs - exactly uvicorn's own setup at process start.
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_root = logging.getLogger("uvicorn")
    other = logging.getLogger("some_other_app_logger")
    for logger in (uvicorn_error, uvicorn_root, other):
        logger.disabled = False

    migrations_module.run_migrations()

    for logger in (uvicorn_error, uvicorn_root, other):
        assert logger.disabled is False, (
            f"run_migrations()'s Alembic invocation disabled {logger.name!r} - "
            "this is exactly what silences uvicorn's startup/request logs and "
            "makes a successful startup look hung"
        )


def test_app_loggers_keep_their_level_after_migrations(tmp_path, monkeypatch):
    """The same root cause, one level subtler.

    fileConfig() also *reconfigures* the root logger from alembic.ini, which
    sets it to WARN. Any app logger without an explicit level of its own then
    inherits WARN for the rest of the process, so everything the email poller
    and the rest of the app report at INFO silently disappears in production
    while errors still show - the worst kind of half-working logging.
    """
    from app.core.logging import configure_logging, get_logger

    db_path = tmp_path / "logging_level_check.db"
    monkeypatch.setattr(
        migrations_module.settings, "database_url", f"sqlite:///{db_path.as_posix()}"
    )

    configure_logging()
    poller_logger = get_logger("app.services.email_poller")
    assert poller_logger.isEnabledFor(logging.INFO)

    migrations_module.run_migrations()

    assert poller_logger.isEnabledFor(logging.INFO), (
        "application INFO logging was silenced by Alembic reconfiguring the "
        "root logger - the poller's operational output would vanish"
    )
