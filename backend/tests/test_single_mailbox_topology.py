"""One mailbox serving as both the complaints inbox and the ticket destination.

This is the production topology when a deployment has a single mailbox:
IMAP_USERNAME == SMTP sender == SUPPORT_INBOX_ADDRESS. Every completed ticket
is therefore emailed *into the mailbox the poller reads*, which is exactly the
shape that turns into a runaway loop if self-addressed mail is not filtered:
notification -> read as a complaint -> reply -> ticket -> notification...

The guarantee under test is that the system still does all its real work
(replies, tickets, notifications) while never once answering itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.models.conversation import Conversation
from app.email.factory import get_email_provider
from app.services import email_poller as poller_module
from app.services.email_poller import EmailPoller

MAILBOX = "complaints@example.com"
CUSTOMER = "single-mailbox-customer@example.com"


@pytest.fixture()
def single_mailbox(client, monkeypatch):
    """Point every address setting at one mailbox, as a one-mailbox deploy does."""
    for module_settings in (poller_module.settings,):
        monkeypatch.setattr(module_settings, "imap_username", MAILBOX)
        monkeypatch.setattr(module_settings, "support_inbox_address", MAILBOX)
        monkeypatch.setattr(module_settings, "smtp_from_addr", None)

    provider = get_email_provider()
    provider.support_address = MAILBOX
    provider._inbox.clear()
    provider.sent_box.clear()
    provider.sent_bodies.clear()
    return provider


@pytest.fixture()
def poller(client):
    from app.core.database import SessionLocal

    return EmailPoller(session_factory=SessionLocal)


def test_settings_send_from_the_single_mailbox():
    settings = Settings(
        email_provider="imap_smtp",
        imap_username=MAILBOX,
        support_inbox_address=MAILBOX,
    )
    assert settings.smtp_sender == MAILBOX
    # Nothing is "missing" - a single mailbox is a complete configuration.
    assert settings.missing_email_settings() == [
        "IMAP_HOST",
        "IMAP_PASSWORD",
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
    ]


async def test_full_flow_never_answers_itself(single_mailbox, poller, db_session):
    mailbox = single_mailbox

    # 1. A real complaint arrives, complete enough to produce a ticket at once.
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=CUSTOMER,
            subject="Withdrawal stuck",
            body=(
                "My withdrawal never arrived. user id U-482913, account email "
                "jane.doe@example.com, transaction TXN-9f3a12bc."
            ),
        )
    )
    assert await poller.poll_once() == 1

    to_customer = [e for e in mailbox.sent_bodies if e.to_addr == CUSTOMER]
    to_mailbox = [e for e in mailbox.sent_bodies if e.to_addr == MAILBOX]
    assert len(to_customer) == 1, "the customer must still get their confirmation"
    assert len(to_mailbox) == 1, "the ticket notification must still be sent"
    notification = to_mailbox[0]
    assert notification.subject.startswith("[Ticket ")

    # 2. That notification is delivered into the very mailbox being polled -
    #    the loop scenario. Feed it back exactly as the mail server would.
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr=MAILBOX,
            subject=notification.subject,
            body=notification.body,
        )
    )
    assert await poller.poll_once() == 0, "the system must not process its own mail"

    # Nothing new was sent, and no conversation was opened for our own address.
    assert len(mailbox.sent_bodies) == 2
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.customer.has(email=MAILBOX))
        )
        == 0
    )
    # Acknowledged, so it is not reconsidered on every future poll.
    assert mailbox._inbox == {}

    # 3. Polling again with an empty mailbox stays quiet - no runaway.
    assert await poller.poll_once() == 0
    assert len(mailbox.sent_bodies) == 2
