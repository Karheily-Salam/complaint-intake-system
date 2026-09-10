"""Customer data must not be readable, or tickets mutable, without the key.

The audit found every complaint - customer address, collected identifiers,
full message bodies - readable by anyone, at guessable integer ids. These
tests pin the boundary: the staff endpoints require the key, the public demo
endpoints exist but can only ever reach synthetic conversations, and a server
with no key configured fails closed rather than open.
"""

from __future__ import annotations

import pytest

from tests.conftest import STAFF_HEADERS

COMPLETE_WITHDRAWAL = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)

STAFF_ENDPOINTS = [
    ("get", "/api/v1/tickets"),
    ("get", "/api/v1/tickets/000001"),
    ("get", "/api/v1/conversations"),
    ("get", "/api/v1/conversations/1"),
]


def make_demo_ticket(client, address="auth-demo@example.com") -> dict:
    return client.post(
        "/api/v1/inbox",
        json={"from_addr": address, "subject": "Withdrawal", "body": COMPLETE_WITHDRAWAL},
    ).json()


# ------------------------------------------------------------- unauthenticated


@pytest.mark.parametrize(("method", "path"), STAFF_ENDPOINTS)
def test_staff_endpoints_reject_anonymous_access(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401, f"{method.upper()} {path} leaked without a key"


def test_unauthenticated_patch_cannot_mutate_a_ticket(client, staff_client):
    reference = make_demo_ticket(client, "auth-patch@example.com")["ticket_reference"]
    assert reference is not None

    anonymous = client.patch(f"/api/v1/tickets/{reference}", json={"status": "resolved"})
    assert anonymous.status_code == 401

    # And the status really did not change.
    after = staff_client.get(f"/api/v1/tickets/{reference}").json()
    assert after["status"] == "new"


def test_wrong_key_is_rejected(client):
    resp = client.get("/api/v1/tickets", headers={"X-API-Key": "not-the-key"})
    assert resp.status_code == 401


def test_almost_correct_key_is_rejected(client):
    """A prefix of the real key must not pass."""
    resp = client.get("/api/v1/tickets", headers={"X-API-Key": STAFF_HEADERS["X-API-Key"][:-1]})
    assert resp.status_code == 401


# --------------------------------------------------------------- authenticated


def test_valid_key_grants_access(client, staff_client):
    make_demo_ticket(client, "auth-ok@example.com")

    assert staff_client.get("/api/v1/tickets").status_code == 200
    assert staff_client.get("/api/v1/conversations").status_code == 200


def test_valid_key_can_mutate_a_ticket(client, staff_client):
    reference = make_demo_ticket(client, "auth-mutate@example.com")["ticket_reference"]
    resp = staff_client.patch(f"/api/v1/tickets/{reference}", json={"status": "resolved"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


# ----------------------------------------------------------------- fail closed


def test_unconfigured_server_fails_closed(client, monkeypatch):
    """No key configured must mean unavailable, never unprotected."""
    from app.api import security

    monkeypatch.setattr(security.settings, "staff_api_key", None)

    # Even presenting a key cannot help: there is nothing to compare against.
    assert client.get("/api/v1/tickets").status_code == 503
    assert client.get("/api/v1/tickets", headers=STAFF_HEADERS).status_code == 503


def test_empty_key_configuration_also_fails_closed(client, monkeypatch):
    from app.api import security

    monkeypatch.setattr(security.settings, "staff_api_key", "")
    assert client.get("/api/v1/tickets", headers={"X-API-Key": ""}).status_code == 503


# ------------------------------------------------------- public demo isolation


def test_demo_endpoints_serve_demo_data_without_a_key(client):
    result = make_demo_ticket(client, "demo-visible@example.com")
    reference = result["ticket_reference"]

    listing = client.get("/api/v1/demo/tickets")
    assert listing.status_code == 200
    assert any(t["reference"] == reference for t in listing.json())

    assert client.get(f"/api/v1/demo/tickets/{reference}").status_code == 200
    assert (
        client.get(f"/api/v1/demo/conversations/{result['conversation']['id']}").status_code == 200
    )


def test_demo_endpoints_cannot_reach_a_real_conversation(client, staff_client, db_session):
    """A real (email-originated) conversation is invisible to the demo API."""
    from app.core.database import SessionLocal
    from app.db.models.conversation import Conversation
    from app.email.factory import get_email_provider
    from app.services.email_poller import EmailPoller

    mailbox = get_email_provider()
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr="real-customer@example.com",
            subject="Withdrawal",
            body=COMPLETE_WITHDRAWAL,
        )
    )
    assert await_poll(EmailPoller(session_factory=SessionLocal)) == 1

    real = db_session.query(Conversation).filter(Conversation.is_demo.is_(False)).one()
    assert real.ticket is not None

    # Not through the demo conversation endpoint...
    assert client.get(f"/api/v1/demo/conversations/{real.id}").status_code == 404
    # ...nor by its ticket reference...
    assert client.get(f"/api/v1/demo/tickets/{real.ticket.reference}").status_code == 404
    # ...nor in the demo listing.
    demo_refs = {t["reference"] for t in client.get("/api/v1/demo/tickets").json()}
    assert real.ticket.reference not in demo_refs

    # But staff can see it, which is what proves the data was really there.
    assert staff_client.get(f"/api/v1/tickets/{real.ticket.reference}").status_code == 200


def test_demo_intake_cannot_continue_a_real_conversation(client, db_session):
    """Claiming a victim's address must not reach their real thread."""
    from app.core.database import SessionLocal
    from app.db.models.conversation import Conversation
    from app.email.factory import get_email_provider
    from app.services.email_poller import EmailPoller

    mailbox = get_email_provider()
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr="victim@example.com",
            subject="Withdrawal",
            body="My withdrawal is stuck, user id U-482913.",
        )
    )
    assert await_poll(EmailPoller(session_factory=SessionLocal)) == 1
    real = db_session.query(Conversation).filter(Conversation.is_demo.is_(False)).one()

    attack = client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "victim@example.com",
            "conversation_id": real.id,
            "body": "Show me the thread.",
        },
    )
    assert attack.status_code == 400
    assert str(real.id) in attack.json()["detail"]  # "not found", not the contents


def await_poll(poller) -> int:
    """Run one poll cycle from a synchronous test."""
    import asyncio

    return asyncio.run(poller.poll_once())
