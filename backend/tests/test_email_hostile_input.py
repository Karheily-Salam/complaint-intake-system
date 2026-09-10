"""Hostile inbound mail must not break, wedge, or exploit the system.

Everything in an inbound email is written by a stranger. The dangerous case
here is not classic header injection - Python's email package rejects CR/LF in
a header outright - but the consequence of that rejection: if a value we echo
back into a reply contains a newline, send() raises *every single time*, the
message is never acknowledged, and the poller retries it every interval
forever. A subject can carry a newline despite RFC 5322 folding, because an
encoded-word is decoded after unfolding.
"""

from __future__ import annotations

import base64
import email
import email.policy

import pytest
from sqlalchemy import func, select

from app.db.models.conversation import Conversation
from app.email.base import (
    MAX_BODY_CHARS,
    MAX_MESSAGE_ID_CHARS,
    MAX_REFERENCES,
    InboundEmail,
    OutboundEmail,
)
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


# ------------------------------------------------------------- sanitisation


@pytest.mark.parametrize(
    "hostile",
    [
        "Refund\nBcc: victim@evil.com",
        "Refund\r\nBcc: victim@evil.com",
        "Refund\x00\x07 request",
    ],
)
def test_control_characters_are_stripped_from_subjects(hostile):
    parsed = InboundEmail(
        message_id="<a@b>", from_addr="x@y.com", to_addr="z@y.com", subject=hostile, body="hi"
    )
    assert "\n" not in (parsed.subject or "")
    assert "\r" not in (parsed.subject or "")
    assert "\x00" not in (parsed.subject or "")
    # The readable part survives - this is sanitising, not discarding.
    assert "Refund" in parsed.subject


def test_a_sanitised_subject_can_actually_be_sent(monkeypatch):
    """The whole point: building the reply must no longer raise."""
    from tests.test_imap_smtp_provider import StubSmtp, make_provider

    payload = base64.b64encode(b"Refund\nBcc: victim@evil.com").decode()
    raw = (
        f"Message-ID: <evil@x>\r\nFrom: a@b.com\r\nTo: c@d.com\r\n"
        f"Subject: =?utf-8?B?{payload}?=\r\n\r\nbody\r\n"
    ).encode()
    decoded = str(email.message_from_bytes(raw, policy=email.policy.default).get("Subject"))
    assert "\n" in decoded, "precondition: the decoded header really does contain a newline"

    inbound = InboundEmail(
        message_id="<evil@x>", from_addr="a@b.com", to_addr="c@d.com", subject=decoded, body="b"
    )

    provider = make_provider()
    smtp = StubSmtp()
    monkeypatch.setattr(provider, "_smtp_connect", lambda: smtp)

    import asyncio

    asyncio.run(
        provider.send(
            OutboundEmail(to_addr="a@b.com", subject=f"Re: {inbound.subject}", body="reply")
        )
    )
    assert "Bcc" not in smtp.sent[0].as_string().split("\n\n", 1)[0].replace("Bcc: ", "X")
    assert smtp.sent[0]["Bcc"] is None


def test_outbound_subject_is_sanitised_even_from_a_legacy_stored_value():
    out = OutboundEmail(to_addr="a@b.com", subject="Re: x\nBcc: evil@x.com", body="b")
    assert "\n" not in out.subject


def test_oversized_body_is_truncated():
    huge = "A" * (MAX_BODY_CHARS + 50_000)
    parsed = InboundEmail(
        message_id="<a@b>", from_addr="x@y.com", to_addr="z@y.com", body=huge
    )
    assert len(parsed.body) <= MAX_BODY_CHARS + 100
    assert parsed.body.endswith("[message truncated]")


def test_absurd_references_chain_is_bounded():
    parsed = InboundEmail(
        message_id="<a@b>",
        from_addr="x@y.com",
        to_addr="z@y.com",
        body="hi",
        references=[f"<{i}@spam>" for i in range(500)] + ["", "   "],
    )
    assert len(parsed.references) == MAX_REFERENCES
    assert all(r.strip() for r in parsed.references)


def test_overlong_message_id_is_bounded_to_its_column():
    parsed = InboundEmail(
        message_id="<" + "x" * 5000 + "@evil>",
        from_addr="x@y.com",
        to_addr="z@y.com",
        body="hi",
    )
    assert len(parsed.message_id) <= MAX_MESSAGE_ID_CHARS


# --------------------------------------------------------------- end to end


async def test_hostile_email_is_processed_once_and_not_retried_forever(
    mailbox, poller, db_session
):
    """The poison pill: previously this email could never be acknowledged."""
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr="hostile@example.com",
            subject="Withdrawal\r\nBcc: victim@evil.com",
            body="My withdrawal is stuck, user id U-482913.",
        )
    )

    assert await poller.poll_once() == 1
    assert mailbox._inbox == {}, "must be acknowledged, not left to retry forever"
    assert len(mailbox.sent_bodies) == 1
    assert "\n" not in mailbox.sent_bodies[0].subject

    # And a second poll does nothing, rather than reprocessing it.
    assert await poller.poll_once() == 0
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.customer.has(email="hostile@example.com"))
        )
        == 1
    )
