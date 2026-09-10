"""Two inbound emails arriving at effectively the same moment.

The goal is narrow and deliberate: prove the *current* single-worker design is
safe, not to build distributed concurrency machinery it does not have. Each
email is processed in its own session and committed before acknowledgement, so
concurrent arrivals must not merge threads, duplicate tickets, or lose a
complaint - and a replayed message must stay idempotent even when it races
against its own original.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.ticket import Ticket
from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller

COMPLETE = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)


@pytest.fixture()
def mailbox(client):
    return get_email_provider()


@pytest.fixture()
def poller(client):
    from app.core.database import SessionLocal

    return EmailPoller(session_factory=SessionLocal)


def count(db_session, model, **filters) -> int:
    stmt = select(func.count()).select_from(model)
    for attr, value in filters.items():
        stmt = stmt.where(getattr(model, attr) == value)
    return db_session.scalar(stmt)


async def test_two_different_customers_at_once_stay_separate(mailbox, poller, db_session):
    from app.core.database import SessionLocal
    from app.services.intake_service import IntakeService

    first = mailbox.make_inbound(
        from_addr="concurrent-a@example.com", subject="Withdrawal", body=COMPLETE
    )
    second = mailbox.make_inbound(
        from_addr="concurrent-b@example.com", subject="Withdrawal", body=COMPLETE
    )

    async def process(inbound):
        db = SessionLocal()
        try:
            return await IntakeService(db).handle_inbound_email(inbound)
        finally:
            db.close()

    results = await asyncio.gather(process(first), process(second))

    assert all(r is not None for r in results)
    assert count(db_session, Conversation) == 2
    assert count(db_session, Ticket) == 2
    references = {r.ticket_reference for r in results}
    assert len(references) == 2, f"tickets collided: {references}"


async def test_the_same_message_processed_twice_concurrently_is_idempotent(
    mailbox, poller, db_session
):
    """The unique index is the backstop when two workers race on one email."""
    from app.core.database import SessionLocal
    from app.services.intake_service import IntakeService

    email = mailbox.make_inbound(
        from_addr="concurrent-dupe@example.com", subject="Withdrawal", body=COMPLETE
    )

    async def process():
        db = SessionLocal()
        try:
            return await IntakeService(db).handle_inbound_email(email)
        except Exception:
            # A losing racer may hit the unique constraint; that is the
            # constraint doing its job, and it must not corrupt anything.
            db.rollback()
            return None
        finally:
            db.close()

    await asyncio.gather(process(), process())

    assert count(db_session, Message, external_message_id=email.message_id) == 1
    assert count(db_session, Conversation) == 1
    assert count(db_session, Ticket) == 1


async def test_two_replies_to_one_thread_do_not_lose_either_message(
    mailbox, poller, db_session
):
    """A customer who sends two emails in quick succession loses neither."""
    from app.core.database import SessionLocal
    from app.services.intake_service import IntakeService

    address = "concurrent-thread@example.com"
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=address,
            subject="Withdrawal stuck",
            body="My withdrawal is stuck, user id U-482913.",
        )
    )
    assert await poller.poll_once() == 1
    our_reply_id = mailbox.sent_box[-1].message_id

    followups = [
        mailbox.make_inbound(
            from_addr=address,
            subject="Re: Withdrawal stuck",
            body="My account email is jane.doe@example.com.",
            in_reply_to=our_reply_id,
        ),
        mailbox.make_inbound(
            from_addr=address,
            subject="Re: Withdrawal stuck",
            body="The transaction id is TXN-9f3a12bc.",
            in_reply_to=our_reply_id,
        ),
    ]

    async def process(inbound):
        db = SessionLocal()
        try:
            return await IntakeService(db).handle_inbound_email(inbound)
        finally:
            db.close()

    await asyncio.gather(*(process(f) for f in followups))

    # One thread, both messages recorded, and both answers captured.
    assert count(db_session, Conversation) == 1
    conversation = db_session.scalar(select(Conversation))
    db_session.refresh(conversation)
    inbound_bodies = [m.body for m in conversation.messages if m.direction == "inbound"]
    assert len(inbound_bodies) == 3, f"a message was lost: {inbound_bodies}"

    collected = {f.key: f.value for f in conversation.complaint.fields if f.value}
    assert collected.get("account_email") == "jane.doe@example.com"
    assert collected.get("withdrawal_transaction_id") == "TXN-9f3a12bc"
    # Exactly one ticket, despite two near-simultaneous completions.
    assert count(db_session, Ticket) == 1
