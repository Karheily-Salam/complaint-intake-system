"""The public demo can never make production send mail to a chosen address.

``POST /api/v1/inbox`` needs no credential and takes the sender address from
the request body. With a real transport configured that was an open path to
"make this server email anyone": the recipient is attacker-chosen, and the
values the attacker supplies come back quoted in the ticket confirmation.

The rule: a demo conversation never touches the real transport. It is answered
by a simulated provider, and the reply is still returned in the response and
stored on the thread.

These tests configure a real (non-simulated) provider, the only situation where
the bug existed. With the mock configured, as in local development and the rest
of the suite, the same provider object is used as before.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.email_log import EmailLog
from app.email.base import EmailProvider, InboundEmail, OutboundEmail, SentEmail
from app.email.factory import get_demo_email_provider, get_email_provider
from app.services.intake_service import IntakeService

COMPLETE_WITHDRAWAL = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)


class RealTransportSpy(EmailProvider):
    """Stands in for the production IMAP/SMTP provider.

    ``is_simulated`` is False, exactly like the real one, and every send is
    recorded rather than performed - so a test can assert that nothing the
    demo does ever reaches it.
    """

    name = "imap_smtp"
    is_simulated = False

    def __init__(self) -> None:
        self.sent: list[OutboundEmail] = []

    async def send(self, email: OutboundEmail) -> SentEmail:
        self.sent.append(email)
        return SentEmail(
            message_id=f"<{uuid.uuid4().hex}@example.com>",
            to_addr=email.to_addr,
            subject=email.subject,
        )

    async def fetch_new(self) -> list[InboundEmail]:
        return []

    async def mark_processed(self, message_id: str) -> None:
        return None


@pytest.fixture()
def production_transport(monkeypatch):
    """Make the application behave as it does in production: a real transport."""
    spy = RealTransportSpy()
    monkeypatch.setattr("app.services.intake_service.get_email_provider", lambda: spy)
    monkeypatch.setattr("app.email.factory.get_email_provider", lambda: spy)
    monkeypatch.setattr("app.services.ticket_reply_service.get_email_provider", lambda: spy)
    return spy


def post(client, body, from_addr="attacker-chosen@example.com", **extra):
    response = client.post(
        "/api/v1/inbox", json={"from_addr": from_addr, "body": body, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------ the boundary


def test_the_public_demo_never_reaches_the_real_transport(client, production_transport):
    post(client, "My withdrawal has not arrived, please help me")
    assert production_transport.sent == [], (
        "the public demo endpoint sent real email to an address from the request"
    )


def test_a_completed_demo_ticket_sends_neither_confirmation_nor_notification(
    client, production_transport
):
    result = post(client, COMPLETE_WITHDRAWAL, from_addr="victim@example.com")
    assert result["ticket_reference"], "the message is complete enough to create a ticket"
    # Two sends would happen on the real path: the customer confirmation and
    # the internal ticket notification to the support inbox.
    assert production_transport.sent == []


def test_the_demo_still_works_and_the_reply_is_visible(client, production_transport):
    result = post(client, "My withdrawal has not arrived, please help me")

    assert result["reply_body"], "the demo must still show the system's reply"
    directions = [m["direction"] for m in result["conversation"]["messages"]]
    assert directions == ["inbound", "outbound"], "the thread still records both messages"
    # And the simulated transport is what handled it.
    assert get_demo_email_provider().sent_bodies


def test_the_email_log_names_the_transport_that_actually_handled_it(
    client, production_transport, db_session
):
    post(client, "My withdrawal has not arrived, please help me")
    providers = {
        row.provider for row in db_session.scalars(select(EmailLog)) if row.direction == "outbound"
    }
    assert providers == {"mock"}, f"outbound demo mail logged as {providers}"


def test_an_injected_value_is_never_mailed_anywhere(client, production_transport):
    """The confirmation quotes collected values back - that is the payload risk."""
    post(
        client,
        "My withdrawal never arrived. user id CLICK-HERE-http://evil.example, "
        "account email a@b.co, transaction TXN-00112233.",
        from_addr="target@example.com",
    )
    assert production_transport.sent == []


# ------------------------------------------------------------------ the real path still sends


async def test_real_inbound_email_still_uses_the_real_transport(
    db_session, production_transport
):
    result = await IntakeService(db_session).handle_inbound_email(
        InboundEmail(
            message_id=f"<{uuid.uuid4().hex}@example.com>",
            from_addr="real.customer@example.com",
            to_addr="complaints@example.com",
            body=COMPLETE_WITHDRAWAL,
        )
    )
    assert result is not None and result.ticket_reference
    recipients = [e.to_addr for e in production_transport.sent]
    assert "real.customer@example.com" in recipients, "a real customer must still be answered"
    assert len(production_transport.sent) == 2, "customer confirmation + ticket notification"


# ------------------------------------------------------------------ unchanged locally


def test_with_the_mock_configured_there_is_still_one_mailbox(client):
    """No test or local workflow changes: same provider object as before."""
    assert get_demo_email_provider() is get_email_provider()

    post(client, "My withdrawal has not arrived, please help me")
    assert get_email_provider().sent_bodies, "the configured mock still captures demo mail"


def test_staff_replies_to_demo_tickets_were_already_refused(
    client, staff_client, production_transport
):
    """The other half of the boundary, which already existed - pinned here so
    the two halves cannot drift apart."""
    reference = post(client, COMPLETE_WITHDRAWAL)["ticket_reference"]
    response = staff_client.post(
        f"/api/v1/tickets/{reference}/reply", json={"body": "hello"}
    )
    assert response.status_code == 409
    assert production_transport.sent == []
