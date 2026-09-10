"""End-to-end tests for the real email pipeline.

These exercise the production path - ``EmailPoller`` -> ``EmailProvider`` ->
``IntakeService.handle_inbound_email`` - rather than the ``POST /inbox``
development simulator, using the mock provider as a stand-in mailbox. The mock
deliberately mirrors IMAP semantics (a message stays in the mailbox until it is
explicitly acknowledged), so retry and idempotency behaviour is genuinely
tested rather than assumed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller

CUSTOMER = "threading@example.com"


@pytest.fixture()
def mailbox(client):
    """The mock provider singleton IntakeService also resolves, emptied."""
    provider = get_email_provider()
    provider._inbox.clear()
    provider.sent_box.clear()
    provider.sent_bodies.clear()
    return provider


@pytest.fixture()
def poller(client):
    # The `client` fixture rebinds SessionLocal to the test engine.
    from app.core.database import SessionLocal

    return EmailPoller(session_factory=SessionLocal)


def to_customer(mailbox, customer: str = CUSTOMER):
    return [e for e in mailbox.sent_bodies if e.to_addr == customer]


def to_support(mailbox):
    return [e for e in mailbox.sent_bodies if e.to_addr == settings.support_inbox_address]


async def deliver_and_poll(mailbox, poller, **kwargs) -> int:
    mailbox.deliver(mailbox.make_inbound(**kwargs))
    return await poller.poll_once()


# --------------------------------------------------------------- basic intake


async def test_inbound_email_creates_conversation_and_asks_one_field(
    mailbox, poller, db_session
):
    processed = await deliver_and_poll(
        mailbox,
        poller,
        from_addr=CUSTOMER,
        subject="Withdrawal stuck",
        body="Hello, my withdrawal has not arrived. My user id is U-482913.",
    )
    assert processed == 1

    replies = to_customer(mailbox)
    assert len(replies) == 1
    # One field at a time: it asks for the account email, not everything left.
    assert "email" in replies[0].body.lower()
    assert "transaction" not in replies[0].body.lower()
    # The opaque thread reference is present for subject-based fallback.
    assert "[Ref:" in replies[0].subject

    # Acknowledged only after the turn was committed, so the mailbox is empty.
    assert mailbox._inbox == {}


async def test_threading_uses_headers_not_sender_address(mailbox, poller, db_session):
    address = "headers@example.com"
    await deliver_and_poll(
        mailbox,
        poller,
        from_addr=address,
        subject="Withdrawal stuck",
        body=(
            "My withdrawal is stuck. user id U-482913, account email "
            "jane.doe@example.com."
        ),
    )
    our_message_id = mailbox.sent_box[-1].message_id

    # A reply with a completely different subject: only In-Reply-To can route it.
    await deliver_and_poll(
        mailbox,
        poller,
        from_addr=address,
        subject="(no subject)",
        body="The transaction id is TXN-9f3a12bc.",
        in_reply_to=our_message_id,
    )

    conversations = db_session.scalars(
        select(Conversation).where(Conversation.customer.has(email=address))
    ).all()
    assert len(conversations) == 1
    assert conversations[0].ticket is not None


async def test_threading_falls_back_to_subject_token(mailbox, poller, db_session):
    await deliver_and_poll(
        mailbox,
        poller,
        from_addr="token@example.com",
        subject="Withdrawal stuck",
        body="Withdrawal problem. user id U-482913, email jane.doe@example.com.",
    )
    tagged_subject = mailbox.sent_bodies[-1].subject

    # No threading headers at all - the provider rewrote the Message-ID.
    await deliver_and_poll(
        mailbox,
        poller,
        from_addr="token@example.com",
        subject=tagged_subject,
        body="Transaction id TXN-9f3a12bc.",
    )

    conversations = db_session.scalars(
        select(Conversation).where(Conversation.customer.has(email="token@example.com"))
    ).all()
    assert len(conversations) == 1
    assert conversations[0].ticket is not None


async def test_same_customer_two_unrelated_emails_are_separate_conversations(
    mailbox, poller, db_session
):
    for subject in ("Withdrawal stuck", "Deposit missing"):
        await deliver_and_poll(
            mailbox,
            poller,
            from_addr="multi@example.com",
            subject=subject,
            body=f"I have a problem: {subject}.",
        )

    conversations = db_session.scalars(
        select(Conversation).where(Conversation.customer.has(email="multi@example.com"))
    ).all()
    assert len(conversations) == 2


# ----------------------------------------------------------------- idempotency


async def test_duplicate_delivery_is_ignored(mailbox, poller, db_session):
    email = mailbox.make_inbound(
        from_addr="dupe@example.com",
        subject="Withdrawal stuck",
        body="My withdrawal failed, user id U-482913.",
    )
    mailbox.deliver(email)
    assert await poller.poll_once() == 1
    replies_after_first = len(mailbox.sent_bodies)

    # The very same email is delivered again (e.g. the acknowledgement was
    # lost and IMAP handed it back on the next poll).
    mailbox.deliver(email)
    await poller.poll_once()

    conversations = db_session.scalars(
        select(Conversation).where(Conversation.customer.has(email="dupe@example.com"))
    ).all()
    assert len(conversations) == 1
    assert len(mailbox.sent_bodies) == replies_after_first  # no duplicate reply
    stored = db_session.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.external_message_id == email.message_id)
    )
    assert stored == 1


# ------------------------------------------------------- ticket notification


async def test_completed_ticket_is_emailed_to_support_inbox(mailbox, poller, db_session):
    await deliver_and_poll(
        mailbox,
        poller,
        from_addr="ticket@example.com",
        subject="Withdrawal stuck",
        body=(
            "My withdrawal never arrived. user id U-482913, account email "
            "jane.doe@example.com, transaction TXN-9f3a12bc."
        ),
    )

    notifications = to_support(mailbox)
    assert len(notifications) == 1
    note = notifications[0]
    conversation = db_session.scalar(
        select(Conversation).where(Conversation.customer.has(email="ticket@example.com"))
    )
    reference = conversation.ticket.reference

    assert reference in note.subject
    assert "Withdrawal problem" in note.body
    assert "ticket@example.com" in note.body
    for value in ("U-482913", "jane.doe@example.com", "TXN-9f3a12bc"):
        assert value in note.body
    # Field labels, not internal keys or database identifiers.
    assert "User ID" in note.body
    assert "user_id" not in note.body
    assert "conversation_id" not in note.body
    assert f"id: {conversation.id}" not in note.body

    # The customer separately received their own confirmation with the same
    # reference - the support copy is not sent instead of it.
    customer_replies = to_customer(mailbox, "ticket@example.com")
    assert any(reference in reply.body for reply in customer_replies)


async def test_support_is_not_notified_twice_for_one_ticket(mailbox, poller, db_session):
    await deliver_and_poll(
        mailbox,
        poller,
        from_addr="once@example.com",
        subject="Withdrawal stuck",
        body=(
            "Withdrawal failed. user id U-482913, account email "
            "jane.doe@example.com, transaction TXN-9f3a12bc."
        ),
    )
    assert len(to_support(mailbox)) == 1
    our_message_id = mailbox.sent_box[-1].message_id

    # A follow-up on the finished thread refreshes the ticket but must not
    # re-notify support about a ticket they already have.
    await deliver_and_poll(
        mailbox,
        poller,
        from_addr="once@example.com",
        subject="Re: Withdrawal stuck",
        body="Sorry, the correct transaction id is TXN-000111aa.",
        in_reply_to=our_message_id,
    )
    assert len(to_support(mailbox)) == 1


# ------------------------------------------------------------ failure / retry


async def test_send_failure_rolls_back_and_leaves_email_for_retry(
    mailbox, poller, db_session, monkeypatch
):
    original_send = mailbox.send
    calls = {"n": 0}

    async def failing_send(email):
        calls["n"] += 1
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr(mailbox, "send", failing_send)

    processed = await deliver_and_poll(
        mailbox,
        poller,
        from_addr="retry@example.com",
        subject="Withdrawal stuck",
        body="My withdrawal is stuck, user id U-482913.",
    )
    assert processed == 0
    assert calls["n"] == 1
    # Not acknowledged, so it will be retried...
    assert len(mailbox._inbox) == 1
    # ...and nothing was half-persisted in the meantime.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.customer.has(email="retry@example.com"))
        )
        == 0
    )

    monkeypatch.setattr(mailbox, "send", original_send)
    assert await poller.poll_once() == 1
    assert mailbox._inbox == {}
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.customer.has(email="retry@example.com"))
        )
        == 1
    )
    assert len(to_customer(mailbox, "retry@example.com")) == 1


async def test_one_bad_email_does_not_block_the_rest_of_the_batch(
    mailbox, poller, db_session, monkeypatch
):
    good = mailbox.make_inbound(
        from_addr="good@example.com",
        subject="Withdrawal stuck",
        body="My withdrawal is stuck, user id U-482913.",
    )
    bad = mailbox.make_inbound(
        from_addr="bad@example.com",
        subject="Withdrawal stuck",
        body="POISON my withdrawal is stuck.",
    )
    mailbox.deliver(bad)
    mailbox.deliver(good)

    from app.services.intake_service import IntakeService

    original = IntakeService.handle_inbound_email

    async def selectively_failing(self, inbound):
        if "POISON" in inbound.body:
            raise RuntimeError("engine exploded")
        return await original(self, inbound)

    monkeypatch.setattr(IntakeService, "handle_inbound_email", selectively_failing)

    assert await poller.poll_once() == 1
    assert list(mailbox._inbox) == [bad.message_id]
    assert len(to_customer(mailbox, "good@example.com")) == 1


# ------------------------------------------------------------------ languages


@pytest.mark.parametrize(
    ("address", "body", "expected"),
    [
        ("ar-mail@example.com", "مرحبا، لدي مشكلة في سحب الأموال. لم تصل الأموال.", "ar"),
        ("ru-mail@example.com", "Здравствуйте, у меня проблема с выводом денег.", "ru"),
        ("en-mail@example.com", "Hello, I have a problem with my withdrawal.", "en"),
    ],
)
async def test_reply_language_follows_the_email_language(
    mailbox, poller, db_session, address, body, expected
):
    await deliver_and_poll(mailbox, poller, from_addr=address, subject="Help", body=body)

    conversation = db_session.scalar(
        select(Conversation).where(Conversation.customer.has(email=address))
    )
    assert conversation.language_code == expected
    assert len(to_customer(mailbox, address)) == 1
