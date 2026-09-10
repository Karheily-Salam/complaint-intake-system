"""Operational settings that are easy to lose in a refactor and hard to notice.

SQLite's defaults are wrong for a process that writes from both an HTTP
request and a background poller, and the rate limit on the one public write
endpoint lives in a config file no Python test would otherwise look at.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.core.database import apply_sqlite_pragmas

NGINX_CONF = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"


# ------------------------------------------------------------------- SQLite


def test_wal_and_busy_timeout_are_applied(tmp_path):
    """WAL matters because a writer must not block readers here."""
    db = tmp_path / "pragma_check.db"
    engine = apply_sqlite_pragmas(create_engine(f"sqlite:///{db.as_posix()}"))

    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1

    engine.dispose()


def test_pragmas_are_applied_to_every_connection(tmp_path):
    """PRAGMAs are connection-scoped, so a pooled reconnect must get them too."""
    db = tmp_path / "pragma_pool.db"
    engine = apply_sqlite_pragmas(create_engine(f"sqlite:///{db.as_posix()}"))

    for _ in range(3):
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
    engine.dispose()


def test_non_sqlite_engines_are_left_alone():
    """The helper must be a no-op for a future PostgreSQL engine.

    A stub stands in for the engine so the test needs neither a database nor
    the psycopg driver - only the dialect name is consulted.
    """

    class _NotSqlite:
        class dialect:
            name = "postgresql"

    engine = _NotSqlite()
    assert apply_sqlite_pragmas(engine) is engine  # type: ignore[arg-type]


def test_application_engine_really_has_the_pragmas_attached(client):
    """The engine the app actually uses, not just a freshly built one.

    journal_mode is deliberately not asserted here: the test suite runs
    against an in-memory database, where SQLite reports "memory" and WAL does
    not apply. The file-backed case is covered above; what matters here is
    that the listener is attached to the engine the application uses at all.
    """
    from app.core import database

    if database.engine.dialect.name != "sqlite":
        pytest.skip("not running against SQLite")

    with database.engine.connect() as conn:
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_in_memory_test_engine_still_gets_foreign_keys():
    engine = apply_sqlite_pragmas(
        create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    )
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


# -------------------------------------------------------------- rate limiting


def test_public_intake_endpoint_is_rate_limited():
    conf = NGINX_CONF.read_text(encoding="utf-8")

    assert re.search(r"limit_req_zone\s+\$binary_remote_addr\s+zone=intake:", conf), (
        "the shared memory zone keyed on client address is missing"
    )
    # The limit must be attached to the public write endpoint specifically.
    intake_block = re.search(
        r"location\s*=\s*/api/v1/inbox\s*\{(.*?)\}", conf, re.DOTALL
    )
    assert intake_block, "no exact-match location block for the intake endpoint"
    assert "limit_req zone=intake" in intake_block.group(1)
    assert "proxy_pass" in intake_block.group(1), "the block must still proxy the request"


def test_rate_limit_returns_429_not_a_server_error():
    conf = NGINX_CONF.read_text(encoding="utf-8")
    assert "limit_req_status 429" in conf


def test_rate_limit_allows_a_realistic_demo_conversation():
    """A visitor answering several questions must not be throttled."""
    conf = NGINX_CONF.read_text(encoding="utf-8")
    burst = re.search(r"limit_req zone=intake burst=(\d+)", conf)
    assert burst, "no burst configured - a normal multi-turn demo would be rejected"
    assert int(burst.group(1)) >= 5, "a full complaint takes several messages"
