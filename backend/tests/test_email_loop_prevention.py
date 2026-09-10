"""An automatic responder must not answer automatic mail.

Two ways this system could talk to itself in production:

- The ticket notification it sends to the support inbox comes back into the
  polled mailbox (one plausible misconfiguration: both are the same address).
  Treated as a customer complaint, that is a runaway loop - it would reply to
  itself, ticket itself, and repeat every poll.
- A customer's out-of-office reply is treated as the answer to the question we
  just asked, quietly corrupting the conversation (RFC 3834 forbids replying
  to such mail at all).

Both must be skipped *and* acknowledged: leaving them unread would simply
re-trigger the same decision on every poll forever.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.conversation import Conversation
from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller, our_own_addresses


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


def conversations_for(db_session, address: str) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.customer.has(email=address))
    )


async def test_our_own_ticket_notification_is_not_treated_as_a_complaint(
    mailbox, poller, db_session
):
    """The exact loop: the support-inbox notification returns to the poller."""
    own = settings.support_inbox_address
    assert own in our_own_addresses()

    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=own,
            subject="[Ticket 000123] Withdrawal problem - someone@example.com",
            body="Ticket reference: 000123\nComplaint type: Withdrawal problem",
        )
    )
    processed = await poller.poll_once()

    assert processed == 0
    assert conversations_for(db_session, own) == 0
    assert mailbox.sent_bodies == []  # nothing sent back - the loop is broken
    # Acknowledged, so it is not reconsidered on every future poll.
    assert mailbox._inbox == {}


@pytest.mark.parametrize(
    ("headers", "label"),
    [
        ({"auto_submitted": "auto-replied"}, "out-of-office reply"),
        ({"auto_submitted": "auto-generated"}, "bounce/system notice"),
        ({"precedence": "bulk"}, "bulk mail"),
        ({"precedence": "list"}, "mailing list traffic"),
    ],
)
async def test_auto_generated_mail_is_ignored(mailbox, poller, db_session, headers, label):
    sender = "vacation@example.com"
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=sender,
            subject="Out of office",
            body="I am away until Monday and cannot read your message.",
            **headers,
        )
    )
    processed = await poller.poll_once()

    assert processed == 0, f"{label} must not be answered"
    assert conversations_for(db_session, sender) == 0
    assert mailbox.sent_bodies == []
    assert mailbox._inbox == {}


async def test_auto_submitted_no_is_still_a_real_customer_email(mailbox, poller, db_session):
    """`Auto-Submitted: no` is what an ordinary mail client may send."""
    sender = "genuine@example.com"
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=sender,
            subject="Withdrawal stuck",
            body="My withdrawal has not arrived, user id U-482913.",
            auto_submitted="no",
        )
    )

    assert await poller.poll_once() == 1
    assert conversations_for(db_session, sender) == 1
    assert len(mailbox.sent_bodies) == 1


async def test_outbound_mail_is_marked_auto_replied(monkeypatch):
    """Our own mail must be flagged so other responders do not loop with us."""
    from app.email.base import OutboundEmail
    from tests.test_imap_smtp_provider import StubSmtp, make_provider

    provider = make_provider()
    smtp = StubSmtp()
    monkeypatch.setattr(provider, "_smtp_connect", lambda: smtp)

    await provider.send(
        OutboundEmail(to_addr="jane@example.com", subject="Re: Withdrawal", body="Please...")
    )

    assert smtp.sent[0]["Auto-Submitted"] == "auto-replied"
