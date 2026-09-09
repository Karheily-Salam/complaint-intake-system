from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_complaint_intake.db")
os.environ.setdefault("AI_PROVIDER", "rule_based")
os.environ.setdefault("EMAIL_PROVIDER", "mock")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.main import create_app


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.drop_all(bind=eng)
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture()
def db_session(engine):
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
