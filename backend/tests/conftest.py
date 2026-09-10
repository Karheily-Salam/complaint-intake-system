from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_complaint_intake.db")
os.environ.setdefault("AI_PROVIDER", "rule_based")
os.environ.setdefault("EMAIL_PROVIDER", "mock")
# Tests exercise the staff API with a known key. Never a real credential.
os.environ.setdefault("STAFF_API_KEY", "test-staff-key")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.core.database import apply_sqlite_pragmas
from app.db.base import Base
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
STAFF_KEY = os.environ["STAFF_API_KEY"]
STAFF_HEADERS = {"X-API-Key": STAFF_KEY}


@pytest.fixture()
def engine():
    """A fresh, isolated in-memory database per test.

    Previously this was session-scoped, so every test shared one database and
    saw the rows left by earlier ones. That made outcomes depend on execution
    order and produced at least one false failure (two tests picking the same
    customer address). StaticPool keeps the single in-memory database alive
    across connections within the test, and it disappears afterwards.
    """
    eng = apply_sqlite_pragmas(
        create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    from sqlalchemy.orm import sessionmaker

    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine):
    from app.core import database

    database.engine = engine
    database.SessionLocal.configure(bind=engine)
    return TestClient(create_app())


@pytest.fixture()
def staff_client(client):
    """A separate client carrying the staff API key.

    Deliberately a distinct instance rather than headers added to `client`:
    sharing one object would silently authenticate every request a test makes
    through `client` too, so a test asserting that anonymous access is
    rejected would pass while proving nothing.
    """
    return TestClient(client.app, headers=dict(STAFF_HEADERS))


@pytest.fixture(autouse=True)
def _reset_mock_mailbox():
    """Keep the cached mock provider from leaking mail between tests."""
    from app.email.factory import get_email_provider

    provider = get_email_provider()
    for attr in ("_inbox", "sent_box", "sent_bodies"):
        holder = getattr(provider, attr, None)
        if holder is not None:
            holder.clear()
    yield
