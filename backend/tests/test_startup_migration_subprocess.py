"""Regression test: a real `uvicorn` process (not the in-process TestClient
used by test_fresh_database_startup.py) must actually finish startup and
serve requests - both against a fresh SQLite database and one already at
head - reproducing the exact environment of the reported bug: a real worker
process whose own logging (in particular "INFO: Application startup
complete.") went silent after app.core.migrations.run_migrations() ran, due
to alembic/env.py disabling uvicorn's loggers as a side effect of
`fileConfig()`. See test_migrations_logging.py for the same root cause
verified as a fast unit check.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _base_env(database_url: str) -> dict:
    return {
        **os.environ,
        "DATABASE_URL": database_url,
        "RUN_MIGRATIONS_ON_STARTUP": "true",
        "AI_PROVIDER": "rule_based",
        "EMAIL_PROVIDER": "mock",
        "DEBUG": "false",
    }


def _run_uvicorn_and_check_startup(env: dict, timeout: float = 30.0) -> None:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    reachable = False
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break  # exited early (e.g. the hang/crash this test guards against)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as resp:
                    reachable = resp.status == 200
                    break
            except OSError:
                time.sleep(0.3)

        # Give the log buffer a brief moment to flush the confirmation line
        # that follows immediately after the ASGI server becomes reachable.
        time.sleep(0.5)
    finally:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate(timeout=10)

    assert reachable, f"uvicorn never became reachable within {timeout}s\n--- output ---\n{output}"
    assert "Application startup complete." in output, (
        "uvicorn served a request but never logged startup completion - the "
        "exact reported symptom\n--- output ---\n" + output
    )


def test_real_uvicorn_process_starts_against_a_fresh_database(tmp_path):
    db_path = tmp_path / "fresh_subprocess.db"
    assert not db_path.exists()
    _run_uvicorn_and_check_startup(_base_env(f"sqlite:///{db_path.as_posix()}"))
    assert db_path.exists()


def test_real_uvicorn_process_starts_against_a_database_already_at_head(tmp_path):
    db_path = tmp_path / "at_head_subprocess.db"
    env = _base_env(f"sqlite:///{db_path.as_posix()}")

    # Bring it to head first via the plain Alembic CLI, exactly like the
    # reported repro's manual `alembic upgrade head` step, before starting
    # uvicorn separately.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    _run_uvicorn_and_check_startup(env)
