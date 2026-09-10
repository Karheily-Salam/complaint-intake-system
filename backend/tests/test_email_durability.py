"""Durability guarantees around the inbound email boundary.

Three properties that the rest of the suite assumes but never pins down:

- threading works from the ``References`` chain alone, not just ``In-Reply-To``
  (some clients send only the chain, and older entries are what survives a
  long thread)
- an email is acknowledged to the mail server strictly *after* its transaction
  is committed and visible to other connections - the ordering that decides
  whether a crash loses a complaint or merely repeats one
- conversation state survives the process: a restart mid-conversation must
  continue where it left off, not start over
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller


@pytest.fixture()
def mailbox(client):
    provider = get_email_provider()
    provider._inbox.clear()
    provider.sent_box.clear()
    provider.sent_bodies.clear()
    return provider


@pytest.fixture()
def poller(client):
    from app.core.database import SessionLocal

    return EmailPoller(session_factory=SessionLocal)


# ------------------------------------------------------- References threading


async def test_threading_from_the_references_chain_alone(mailbox, poller, db_session):
    """No In-Reply-To at all - only the References chain identifies the thread."""
    address = "references-only@example.com"
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=address,
            subject="Withdrawal stuck",
            body=(
                "Withdrawal problem. user id U-482913, account email "
                "jane.doe@example.com."
            ),
        )
    )
    await poller.poll_once()
    our_message_id = mailbox.sent_box[-1].message_id

    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=address,
            subject="(unrelated subject)",
            body="Transaction id TXN-9f3a12bc.",
            in_reply_to=None,
            # Our id is the oldest entry, with newer unrelated hops after it -
            # the chain must still be searched, not just its last element.
            references=["<older@elsewhere>", our_message_id, "<newer@relay>"],
        )
    )
    await poller.poll_once()

    conversations = db_session.scalars(
        select(Conversation).where(Conversation.customer.has(email=address))
    ).all()
    assert len(conversations) == 1, "the References chain did not resolve the thread"
    assert conversations[0].ticket is not None


# --------------------------------------------------- commit-before-acknowledge


async def test_email_is_acknowledged_only_after_the_commit_is_visible(mailbox, poller):
    """Ordering is the whole retry guarantee, so assert it directly.

    At the moment mark_processed() runs, the inbound message must already be
    committed and readable from an independent connection. If it were
    acknowledged first, a crash in between would drop the complaint silently -
    the mail server would consider it delivered and nothing would exist.
    """
    from app.core.database import SessionLocal

    observed: dict[str, int] = {}
    original_mark = mailbox.mark_processed

    async def observing_mark(message_id: str):
        # A separate session: only committed data is visible to it.
        probe = SessionLocal()
        try:
            observed[message_id] = probe.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.external_message_id == message_id)
            )
        finally:
            probe.close()
        return await original_mark(message_id)

    mailbox.mark_processed = observing_mark
    email = mailbox.make_inbound(
        from_addr="ordering@example.com",
        subject="Withdrawal stuck",
        body="My withdrawal is stuck, user id U-482913.",
    )
    mailbox.deliver(email)
    assert await poller.poll_once() == 1

    assert observed[email.message_id] == 1, (
        "the email was acknowledged before its transaction was committed - a "
        "crash at that instant would lose the complaint entirely"
    )


# ------------------------------------------------------------ restart safety


async def test_conversation_survives_a_restart_mid_flow(mailbox, poller, db_session):
    """Everything the next turn needs lives in the database, not in memory."""
    address = "restart@example.com"
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=address,
            subject="Проблема с выводом",
            body="Здравствуйте, у меня проблема с выводом денег. Мой user id U-777001.",
        )
    )
    await poller.poll_once()
    first_reply_id = mailbox.sent_box[-1].message_id

    # State the next turn depends on, as persisted.
    conversation = db_session.scalar(
        select(Conversation).where(Conversation.customer.has(email=address))
    )
    db_session.refresh(conversation)
    assert conversation.language_code == "ru"
    assert conversation.pending_field is not None
    pending_before = conversation.pending_field
    thread_token = conversation.thread_token

    # Simulate the restart: drop every ORM object and session-level cache, and
    # rebuild the poller exactly as a fresh process would.
    db_session.expunge_all()
    db_session.close()
    from app.core.database import SessionLocal

    restarted = EmailPoller(session_factory=SessionLocal)

    # The customer replies after the restart, in the same thread.
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=address,
            subject="Re: Проблема с выводом",
            body="Почта аккаунта: post.restart@example.com",
            in_reply_to=first_reply_id,
        )
    )
    assert await restarted.poll_once() == 1

    after = db_session.scalar(
        select(Conversation).where(Conversation.customer.has(email=address))
    )
    db_session.refresh(after)
    # Same conversation continued - not a new one, and nothing was re-asked.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.customer.has(email=address))
        )
        == 1
    )
    assert after.thread_token == thread_token
    assert after.language_code == "ru", "language memory did not survive the restart"
    assert after.pending_field != pending_before, "the flow did not advance after restarting"
    collected = {f.key: f.value for f in after.complaint.fields if f.value}
    assert collected.get("user_id") == "U-777001", "collected field lost across restart"
