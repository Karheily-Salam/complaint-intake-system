"""Standalone script run in a subprocess by test_fresh_database_startup.py.

Boots the real FastAPI app (lifespan included) against a brand-new SQLite
file that does not exist yet, from an arbitrary working directory, and drives
a full intake-to-ticket flow. This is not a pytest test module itself (it is
not named test_*.py) - it is invoked as a plain script so it gets a fresh,
un-cached import of app.core.config / app.core.database, and so it can be run
from a working directory that is deliberately not backend/.

Raises (via a plain AssertionError, causing a non-zero exit) on any failure.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

db_path = Path(os.environ["DATABASE_URL"].removeprefix("sqlite:///"))
assert not db_path.exists(), f"expected a fresh database, but {db_path} already exists"

from app.main import create_app  # noqa: E402 (must come after the freshness check)

with TestClient(create_app()) as client:
    # Lifespan startup has now run - migrations must have been applied already.
    assert db_path.exists(), "sqlite file was never created during startup"

    con = sqlite3.connect(db_path)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    for required in ("alembic_version", "customers", "conversations", "complaints", "tickets"):
        assert required in tables, f"missing table {required!r} after startup; found {tables}"

    r1 = client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "fresh-db-check@example.com",
            "customer_name": "Fresh DB Check",
            "subject": "Cannot withdraw",
            "body": (
                "Hello, I have been trying to withdraw money since yesterday and it "
                "doesn't work. My user id is U-482913 and my account email is "
                "fresh-db-check@example.com."
            ),
        },
    )
    assert r1.status_code == 201, r1.text
    result1 = r1.json()
    conversation_id = result1["conversation"]["id"]
    assert not result1["is_complete"]

    r2 = client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "fresh-db-check@example.com",
            "conversation_id": conversation_id,
            "body": "The withdrawal transaction id is TXN-9f3a12bc.",
        },
    )
    assert r2.status_code == 201, r2.text
    result2 = r2.json()
    assert result2["is_complete"] is True
    assert result2["ticket_reference"], "no ticket was created"

con = sqlite3.connect(db_path)
counts = {
    table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    for table in ("customers", "conversations", "complaints", "tickets")
}
con.close()
assert counts["customers"] == 1, counts
assert counts["conversations"] == 1, counts
assert counts["complaints"] == 1, counts
assert counts["tickets"] == 1, counts

print("OK", counts)
