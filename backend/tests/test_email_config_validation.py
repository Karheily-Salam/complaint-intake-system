"""A half-configured mail provider must fail at startup, not on first customer.

Runs a real uvicorn process (the in-process TestClient would not exercise the
same lifespan/exit behaviour) and checks that it refuses to start, names the
missing variables, and never echoes a configured secret.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

SECRET_SENTINEL = "s3cret-imap-password-value"


def _start_and_capture(env: dict, timeout: float = 60.0) -> str:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        output, _ = proc.communicate(timeout=10)
        raise AssertionError(
            "uvicorn kept running despite an incomplete email configuration"
        ) from None
    return output


def test_startup_fails_and_names_missing_email_settings(tmp_path):
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'cfg.db').as_posix()}",
        "AI_PROVIDER": "rule_based",
        "DEBUG": "false",
        "EMAIL_PROVIDER": "imap_smtp",
        # Deliberately incomplete: IMAP host/username and every SMTP setting
        # are absent.
        "IMAP_PASSWORD": SECRET_SENTINEL,
    }
    output = _start_and_capture(env)

    assert "IMAP_HOST" in output
    assert "IMAP_USERNAME" in output
    assert "SMTP_HOST" in output
    assert "SMTP_USERNAME" in output
    assert "SMTP_PASSWORD" in output
    # The one credential that *was* supplied must not be echoed anywhere.
    assert SECRET_SENTINEL not in output
    assert "IMAP_PASSWORD" not in output  # it is set, so it is not "missing"
