"""The staff reply endpoint.

This is the one place in the application where a support agent causes mail to
leave the system, so the tests concentrate on the two things that would be
expensive to get wrong: who the message can be addressed to, and whether the
agent is told the truth about delivery.
"""

from __future__ import annotations

import asyncio

import pytest

from app.email.base import MAX_BODY_CHARS
from app.email.factory import get_email_provider
from app.email.providers.mock import MockEmailProvider
from app.services.email_poller import EmailPoller
from tests.conftest import STAFF_HEADERS

WITHDRAWAL = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)


@pytest.fixture()
def mailbox(client):
    return get_email_provider()


class LiveProvider(MockEmailProvider):
    """Stands in for a configured real transport (SMTP).

    Behaves like the mock, but reports itself as a real one, which is what the
    reply service keys its delivery reporting and demo guard off.
    """

    name = "imap_smtp"
    is_simulated = False


@pytest.fixture()
def live_provider(monkeypatch, client):
    provider = LiveProvider(support_address="support@example.com")
    monkeypatch.setattr(
        "app.services.ticket_reply_service.get_email_provider", lambda: provider
    )
    return provider


def real_ticket(mailbox, address: str = "customer@example.com") -> None:
    """Create a genuine (non-demo) ticket through the real intake path."""
    from app.core.database import SessionLocal

    mailbox.deliver(
        mailbox.make_inbound(from_addr=address, subject="Withdrawal stuck", body=WITHDRAWAL)
    )
    assert asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once()) == 1


def demo_ticket(client, address: str = "demo@example.com") -> str:
    resp = client.post(
        "/api/v1/inbox", json={"from_addr": address, "subject": "Problem", "body": WITHDRAWAL}
    )
    assert resp.status_code == 201
    return resp.json()["ticket_reference"]


def reference_of(staff_client) -> str:
    return staff_client.get("/api/v1/tickets").json()[0]["reference"]


# --------------------------------------------------------------- access control


def test_reply_requires_staff_authentication(client, mailbox):
    real_ticket(mailbox)

    resp = client.post("/api/v1/tickets/000001/reply", json={"body": "We are looking into it."})

    assert resp.status_code == 401


def test_unauthenticated_reply_sends_nothing(client, mailbox):
    real_ticket(mailbox)
    before = len(mailbox.sent_bodies)

    client.post("/api/v1/tickets/000001/reply", json={"body": "Sneaky."})

    assert len(mailbox.sent_bodies) == before, "an anonymous request caused mail to be sent"


def test_reply_response_contains_no_secrets(client, mailbox, staff_client):
    real_ticket(mailbox)
    reference = reference_of(staff_client)

    resp = staff_client.post(f"/api/v1/tickets/{reference}/reply", json={"body": "Hello."})

    for leak in ("STAFF_API_KEY", STAFF_HEADERS["X-API-Key"], "IMAP_PASSWORD", "SMTP_PASSWORD"):
        assert leak not in resp.text


# ------------------------------------------------------------------- recipient


def test_reply_goes_to_the_ticket_customer(client, mailbox, staff_client):
    real_ticket(mailbox, "known@example.com")
    reference = reference_of(staff_client)

    resp = staff_client.post(
        f"/api/v1/tickets/{reference}/reply", json={"body": "We are on it."}
    )

    assert resp.status_code == 200
    assert resp.json()["to_addr"] == "known@example.com"
    assert mailbox.sent_bodies[-1].to_addr == "known@example.com"


def test_caller_cannot_redirect_the_reply(client, mailbox, staff_client):
    """The request body has no recipient field; supplying one changes nothing."""
    real_ticket(mailbox, "known@example.com")
    reference = reference_of(staff_client)

    resp = staff_client.post(
        f"/api/v1/tickets/{reference}/reply",
        json={
            "body": "Redirected?",
            "to_addr": "attacker@evil.example",
            "to": "attacker@evil.example",
            "recipient": "attacker@evil.example",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["to_addr"] == "known@example.com"
    assert mailbox.sent_bodies[-1].to_addr == "known@example.com"
    assert "attacker@evil.example" not in resp.text


# -------------------------------------------------------------------- threading


def test_reply_subject_continues_the_thread(client, mailbox, staff_client):
    real_ticket(mailbox)
    reference = reference_of(staff_client)

    subject = staff_client.post(
        f"/api/v1/tickets/{reference}/reply", json={"body": "Update."}
    ).json()["subject"]

    assert subject.lower().startswith("re:")
    assert "[Ref:" in subject, "reply lost the thread token, so the answer would not thread back"


def test_edited_subject_keeps_the_thread_token(client, mailbox, staff_client):
    real_ticket(mailbox)
    reference = reference_of(staff_client)

    subject = staff_client.post(
        f"/api/v1/tickets/{reference}/reply",
        json={"body": "Update.", "subject": "Your withdrawal"},
    ).json()["subject"]

    assert subject.startswith("Re: Your withdrawal")
    assert "[Ref:" in subject


def test_reply_points_in_reply_to_at_the_customers_last_email(client, mailbox, staff_client):
    real_ticket(mailbox)
    reference = reference_of(staff_client)

    staff_client.post(f"/api/v1/tickets/{reference}/reply", json={"body": "Update."})

    outbound = mailbox.sent_bodies[-1]
    assert outbound.in_reply_to is not None, "reply had no In-Reply-To header"
    # The mock mints inbound ids as <in-...@mock.local> and outbound as
    # <out-...>, so this also proves it points at the customer's message
    # rather than at one of our own.
    assert outbound.in_reply_to.startswith("<in-")
    assert outbound.in_reply_to in outbound.references


def test_reply_is_recorded_in_the_conversation(client, mailbox, staff_client):
    real_ticket(mailbox)
    reference = reference_of(staff_client)
    detail = staff_client.get(f"/api/v1/tickets/{reference}").json()
    before = len(detail["conversation"]["messages"])

    staff_client.post(
        f"/api/v1/tickets/{reference}/reply", json={"body": "Refund issued today."}
    )

    messages = staff_client.get(f"/api/v1/tickets/{reference}").json()["conversation"]["messages"]
    assert len(messages) == before + 1
    assert messages[-1]["direction"] == "outbound"
    assert messages[-1]["body"] == "Refund issued today."


# ------------------------------------------------------------- honest delivery


def test_mock_provider_does_not_claim_delivery(client, mailbox, staff_client):
    real_ticket(mailbox)
    reference = reference_of(staff_client)

    result = staff_client.post(
        f"/api/v1/tickets/{reference}/reply", json={"body": "Update."}
    ).json()

    assert result["simulated"] is True
    assert result["delivered"] is False
    assert result["provider"] == "mock"
    assert "No real email was delivered" in result["detail"]


def test_real_provider_reports_delivery(client, mailbox, staff_client, live_provider):
    real_ticket(mailbox)
    reference = reference_of(staff_client)

    result = staff_client.post(
        f"/api/v1/tickets/{reference}/reply", json={"body": "Update."}
    ).json()

    assert result["simulated"] is False
    assert result["delivered"] is True
    assert result["provider"] == "imap_smtp"
    assert live_provider.sent_bodies[-1].to_addr == "customer@example.com"


def test_real_provider_refuses_to_mail_a_demo_address(client, staff_client, live_provider):
    """Demo tickets carry synthetic addresses; a live transport must not use them."""
    reference = demo_ticket(client, "someone@example.com")

    resp = staff_client.post(f"/api/v1/tickets/{reference}/reply", json={"body": "Hello."})

    assert resp.status_code == 409
    assert "demo" in resp.json()["detail"].lower()
    assert live_provider.sent_bodies == [], "a real email was sent to a synthetic address"


def test_mock_provider_may_reply_to_a_demo_ticket(client, mailbox, staff_client):
    """The demo guard is about real mail, not about blocking local development."""
    reference = demo_ticket(client)

    resp = staff_client.post(f"/api/v1/tickets/{reference}/reply", json={"body": "Hello."})

    assert resp.status_code == 200
    assert resp.json()["simulated"] is True


# ------------------------------------------------------------------- validation


def test_unknown_ticket_is_404(staff_client):
    resp = staff_client.post("/api/v1/tickets/999999/reply", json={"body": "Hello."})

    assert resp.status_code == 404


def test_empty_reply_is_rejected(client, mailbox, staff_client):
    real_ticket(mailbox)
    reference = reference_of(staff_client)
    before = len(mailbox.sent_bodies)

    path = f"/api/v1/tickets/{reference}/reply"
    assert staff_client.post(path, json={"body": ""}).status_code == 422
    assert staff_client.post(path, json={}).status_code == 422
    assert len(mailbox.sent_bodies) == before


def test_oversized_reply_is_rejected(client, mailbox, staff_client):
    real_ticket(mailbox)
    reference = reference_of(staff_client)

    resp = staff_client.post(
        f"/api/v1/tickets/{reference}/reply", json={"body": "x" * (MAX_BODY_CHARS + 1)}
    )

    assert resp.status_code == 422


def test_reply_does_not_change_ticket_status(client, mailbox, staff_client):
    """Replying is not a status change; the agent decides that separately."""
    real_ticket(mailbox)
    reference = reference_of(staff_client)

    staff_client.post(f"/api/v1/tickets/{reference}/reply", json={"body": "Update."})

    assert staff_client.get(f"/api/v1/tickets/{reference}").json()["status"] == "new"
