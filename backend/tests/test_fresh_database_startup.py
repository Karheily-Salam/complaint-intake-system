"""Regression test for the "fresh clone has no tables" startup failure.

Runs the real app (app.main.create_app + its lifespan) in a subprocess,
from a working directory that is deliberately *not* backend/, against a
SQLite file that does not exist yet. This reproduces the exact conditions
that originally produced::

    sqlite3.OperationalError: no such table: customers

It must pass with no manual ``alembic upgrade head`` and no ad-hoc
``CREATE TABLE`` in application code - migrations must be applied
automatically by the app's startup lifespan (see app/core/migrations.py).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("fresh_startup_check.py")


def test_fresh_database_runs_migrations_and_completes_intake(tmp_path):
    db_path = tmp_path / "fresh_complaint_intake.db"
    assert not db_path.exists()

    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "RUN_MIGRATIONS_ON_STARTUP": "true",
        "AI_PROVIDER": "rule_based",
        "EMAIL_PROVIDER": "mock",
        "DEBUG": "false",
    }

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(tmp_path),  # deliberately not backend/ - the original bug's trigger
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"fresh-database startup check failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert db_path.exists()
