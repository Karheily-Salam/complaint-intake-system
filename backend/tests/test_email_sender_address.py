"""Customer replies must come back to the mailbox the poller reads.

A customer replies to whatever address the message came From. If that is not
the polled mailbox, the reply lands somewhere nothing reads and the
conversation silently dies mid-flow - the customer answers the question and
never hears back. Two mailboxes are involved in a real setup (the polled
complaints mailbox, and the support inbox that receives finished tickets), so
this is easy to get wrong.
"""

from __future__ import annotations

from app.core.config import Settings
from app.email.base import OutboundEmail
from tests.test_imap_smtp_provider import StubSmtp, make_provider

POLLED = "complaints@example.com"
SUPPORT = "support-team@example.com"


def test_sends_from_the_polled_mailbox_not_the_support_inbox():
    settings = Settings(
        email_provider="imap_smtp",
        imap_username=POLLED,
        support_inbox_address=SUPPORT,
    )
    assert settings.smtp_sender == POLLED


def test_explicit_from_address_still_wins():
    settings = Settings(
        email_provider="imap_smtp",
        imap_username=POLLED,
        support_inbox_address=SUPPORT,
        smtp_from_addr="noreply@relay.example.com",
    )
    assert settings.smtp_sender == "noreply@relay.example.com"


def test_mock_setup_without_a_mailbox_falls_back_to_support_address():
    settings = Settings(email_provider="mock", support_inbox_address=SUPPORT)
    assert settings.smtp_sender == SUPPORT


async def test_reply_to_steers_replies_back_to_the_polled_mailbox(monkeypatch):
    """When From is a relay identity, Reply-To must rescue the thread."""
    provider = make_provider(imap_username=POLLED, smtp_from_addr="noreply@relay.example.com")
    smtp = StubSmtp()
    monkeypatch.setattr(provider, "_smtp_connect", lambda: smtp)

    await provider.send(OutboundEmail(to_addr="jane@example.com", subject="Re: x", body="..."))

    msg = smtp.sent[0]
    assert msg["From"] == "noreply@relay.example.com"
    assert msg["Reply-To"] == POLLED


async def test_no_redundant_reply_to_when_sending_from_the_polled_mailbox(monkeypatch):
    provider = make_provider(imap_username=POLLED, smtp_from_addr=POLLED)
    smtp = StubSmtp()
    monkeypatch.setattr(provider, "_smtp_connect", lambda: smtp)

    await provider.send(OutboundEmail(to_addr="jane@example.com", subject="Re: x", body="..."))

    assert smtp.sent[0]["Reply-To"] is None
