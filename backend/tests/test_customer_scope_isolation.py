"""Public demo input must never reach a real customer's record.

The public intake endpoint accepts an unverified sender address. When demo and
real data shared one customer row per email, anyone could claim a real
customer's address and set the display name on that record - which support
then sees in the dashboard and in the ticket notification email. Identity is
now scoped by ``is_demo``, so the two never meet.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.models.conversation import Conversation
from app.db.models.customer import Customer
from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller

SHARED_ADDRESS = "victim@example.com"
COMPLETE = (
    "My withdrawal never arrived. user id U-482913, account email "
    "real.account@corp.example, transaction TXN-9f3a12bc."
)
HOSTILE_NAME = "ACCOUNT CLOSED - CALL 555-0100"


@pytest.fixture()
def mailbox(client):
    return get_email_provider()


def real_email(mailbox, address=SHARED_ADDRESS, body=COMPLETE) -> None:
    """Process one genuine inbound email through the poller."""
    from app.core.database import SessionLocal

    mailbox.deliver(mailbox.make_inbound(from_addr=address, subject="Withdrawal", body=body))
    assert asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once()) == 1


def demo_post(client, address=SHARED_ADDRESS, name=None, body="My withdrawal is stuck."):
    payload = {"from_addr": address, "body": body}
    if name is not None:
        payload["customer_name"] = name
    resp = client.post("/api/v1/inbox", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------ demo customers


def test_demo_intake_creates_a_demo_customer(client, db_session):
    demo_post(client, "new-demo@example.com", name="Demo Person")

    customer = db_session.scalar(
        select(Customer).where(Customer.email == "new-demo@example.com")
    )
    assert customer is not None
    assert customer.is_demo is True
    assert customer.name == "Demo Person"


def test_demo_customer_details_can_still_be_updated(client, db_session):
    """The demo must remain a usable sandbox."""
    demo_post(client, "named-later@example.com", name=None)
    demo_post(client, "named-later@example.com", name="Filled In Later")

    customer = db_session.scalar(
        select(Customer).where(Customer.email == "named-later@example.com")
    )
    assert customer.is_demo is True
    assert customer.name == "Filled In Later"


# --------------------------------------------------- the attack, and the fix


def test_public_intake_cannot_name_an_existing_real_customer(client, mailbox, db_session):
    real_email(mailbox)
    real = db_session.scalar(
        select(Customer).where(Customer.email == SHARED_ADDRESS, Customer.is_demo.is_(False))
    )
    assert real.name is None

    demo_post(client, SHARED_ADDRESS, name=HOSTILE_NAME)

    db_session.expire_all()
    real = db_session.scalar(
        select(Customer).where(Customer.email == SHARED_ADDRESS, Customer.is_demo.is_(False))
    )
    assert real.name is None, "public input reached a real customer record"


def test_hostile_name_never_reaches_staff_facing_data(client, mailbox, staff_client, db_session):
    real_email(mailbox)
    demo_post(client, SHARED_ADDRESS, name=HOSTILE_NAME)

    # Not in the staff ticket listing...
    listing = staff_client.get("/api/v1/tickets")
    assert listing.status_code == 200
    assert HOSTILE_NAME not in listing.text

    # ...and not in the ticket notification email support received.
    from app.core.config import settings

    notifications = [e for e in mailbox.sent_bodies if e.to_addr == settings.support_inbox_address]
    assert notifications, "precondition: a ticket notification was sent"
    assert all(HOSTILE_NAME not in note.body for note in notifications)


def test_the_two_records_are_genuinely_separate_rows(client, mailbox, db_session):
    real_email(mailbox)
    demo_post(client, SHARED_ADDRESS, name=HOSTILE_NAME)

    rows = db_session.scalars(
        select(Customer).where(Customer.email == SHARED_ADDRESS).order_by(Customer.id)
    ).all()
    assert len(rows) == 2
    assert {r.is_demo for r in rows} == {True, False}

    demo_row = next(r for r in rows if r.is_demo)
    real_row = next(r for r in rows if not r.is_demo)
    assert demo_row.name == HOSTILE_NAME  # confined to the sandbox
    assert real_row.name is None
    assert demo_row.id != real_row.id


# ------------------------------------------------- real processing still works


def test_real_inbound_email_populates_the_real_customer(mailbox, db_session):
    """The legitimate path must keep working."""
    from app.core.database import SessionLocal
    from app.services.intake_service import IntakeService

    inbound = mailbox.make_inbound(
        from_addr="genuine@example.com", subject="Withdrawal", body=COMPLETE
    )
    db = SessionLocal()
    try:
        asyncio.run(IntakeService(db).handle_inbound_email(inbound))
    finally:
        db.close()

    customer = db_session.scalar(
        select(Customer).where(Customer.email == "genuine@example.com")
    )
    assert customer is not None
    assert customer.is_demo is False
    assert customer.conversations, "the real conversation is attached to the real customer"


def test_demo_and_real_conversations_never_share_a_customer(client, mailbox, db_session):
    real_email(mailbox)
    demo_post(client, SHARED_ADDRESS)

    for customer in db_session.scalars(select(Customer)).all():
        scopes = {c.is_demo for c in customer.conversations}
        assert len(scopes) <= 1, "a customer owns both demo and real conversations"
        if scopes:
            assert scopes == {customer.is_demo}


def test_demo_isolation_still_holds_for_reads(client, mailbox, db_session):
    real_email(mailbox)
    demo_post(client, SHARED_ADDRESS)

    real_convo = db_session.scalar(
        select(Conversation).where(Conversation.is_demo.is_(False))
    )
    assert client.get(f"/api/v1/demo/conversations/{real_convo.id}").status_code == 404
    assert "real.account@corp.example" not in client.get("/api/v1/demo/tickets").text
