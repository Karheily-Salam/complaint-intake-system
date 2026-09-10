"""The demo seed script deletes data, so its blast radius must be pinned.

`--reset` exists so the demo can be returned to a known state. It is the only
destructive operation in the repository, and the thing that must never happen
is for it to take real customer data with it.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from app.db.models.conversation import Conversation
from app.db.models.customer import Customer
from app.db.models.ticket import Ticket
from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller
from scripts.seed_demo import reset_demo_data


@pytest.fixture()
def mailbox(client):
    return get_email_provider()


def add_real_conversation(mailbox, address="real.customer@corp.example") -> None:
    from app.core.database import SessionLocal

    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=address,
            subject="Withdrawal",
            body=(
                "My withdrawal never arrived. user id U-REALONLY, account email "
                "real@corp.example, transaction TXN-REALONLY."
            ),
        )
    )
    assert asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once()) == 1


def add_demo_conversation(client, address="demo.person@example.com") -> None:
    resp = client.post(
        "/api/v1/inbox",
        json={
            "from_addr": address,
            "subject": "Withdrawal",
            "body": (
                "My withdrawal never arrived. user id U-482913, account email "
                "demo@example.com, transaction TXN-9f3a12bc."
            ),
        },
    )
    assert resp.status_code == 201


def count(db, model, **where) -> int:
    stmt = select(func.count()).select_from(model)
    for attr, value in where.items():
        stmt = stmt.where(getattr(model, attr).is_(value))
    return db.scalar(stmt)


def test_reset_removes_demo_data(client, db_session):
    add_demo_conversation(client)
    assert count(db_session, Conversation, is_demo=True) == 1

    removed = reset_demo_data()

    db_session.expire_all()
    assert removed == 1
    assert count(db_session, Conversation, is_demo=True) == 0
    assert count(db_session, Customer, is_demo=True) == 0


def test_reset_never_touches_real_data(client, mailbox, db_session):
    """The property that matters."""
    add_real_conversation(mailbox)
    add_demo_conversation(client)

    db_session.expire_all()
    real_before = db_session.scalar(select(Conversation).where(Conversation.is_demo.is_(False)))
    real_ticket = real_before.ticket.reference
    real_fields = {f.key: f.value for f in real_before.complaint.fields if f.value}

    reset_demo_data()

    db_session.expire_all()
    assert count(db_session, Conversation, is_demo=False) == 1
    assert count(db_session, Customer, is_demo=False) == 1

    real_after = db_session.scalar(select(Conversation).where(Conversation.is_demo.is_(False)))
    assert real_after.ticket.reference == real_ticket
    assert {f.key: f.value for f in real_after.complaint.fields if f.value} == real_fields
    assert real_after.messages, "the real conversation's messages were deleted"


def test_reset_leaves_real_tickets_intact(client, mailbox, db_session):
    add_real_conversation(mailbox)
    add_demo_conversation(client)
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Ticket)) == 2

    reset_demo_data()

    db_session.expire_all()
    remaining = db_session.scalars(select(Ticket)).all()
    assert len(remaining) == 1
    assert remaining[0].conversation.is_demo is False


def test_reset_on_an_empty_database_is_a_no_op(client, db_session):
    # `client` binds the application session factory to the test database -
    # reset_demo_data() opens its own session through it.
    assert reset_demo_data() == 0


def test_reset_is_repeatable(client, db_session):
    add_demo_conversation(client)
    assert reset_demo_data() == 1
    assert reset_demo_data() == 0
