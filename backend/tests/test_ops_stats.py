"""The operations endpoint must be useful, staff-only, and free of PII.

Its whole purpose is answering "is intake actually working?" - a poller that
has silently stopped looks exactly like a quiet mailbox otherwise. That makes
it worth having, and also worth pinning: an endpoint that grows customer
identifiers becomes a second way to read customer data.
"""

from __future__ import annotations

import asyncio

import pytest

from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller, PollerHealth

COMPLETE = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)


@pytest.fixture()
def mailbox(client):
    return get_email_provider()


def test_ops_stats_requires_the_staff_key(client):
    assert client.get("/api/v1/ops/stats").status_code == 401


def test_ops_stats_reports_ticket_and_conversation_counts(client, staff_client):
    client.post(
        "/api/v1/inbox",
        json={"from_addr": "ops-demo@example.com", "subject": "Withdrawal", "body": COMPLETE},
    )

    stats = staff_client.get("/api/v1/ops/stats").json()

    assert stats["tickets"]["total"] == 1
    assert stats["tickets"]["by_status"] == {"new": 1}
    assert stats["tickets"]["by_type"] == {"withdrawal": 1}
    assert stats["conversations"]["total"] == 1
    assert stats["conversations"]["demo"] == 1
    assert stats["conversations"]["real"] == 0


def test_ops_stats_separates_demo_from_real(client, staff_client, mailbox):
    from app.core.database import SessionLocal

    client.post(
        "/api/v1/inbox",
        json={"from_addr": "ops-demo2@example.com", "subject": "Withdrawal", "body": COMPLETE},
    )
    mailbox.deliver(
        mailbox.make_inbound(from_addr="ops-real@example.com", subject="Withdrawal", body=COMPLETE)
    )
    asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once())

    conversations = staff_client.get("/api/v1/ops/stats").json()["conversations"]
    assert conversations["demo"] == 1
    assert conversations["real"] == 1
    assert conversations["total"] == 2


def test_ops_stats_exposes_poller_health(client, staff_client, mailbox):
    """The reason the endpoint exists."""
    health = PollerHealth()
    poller = EmailPoller(health=health)

    mailbox.deliver(
        mailbox.make_inbound(from_addr="ops-poll@example.com", subject="Withdrawal", body=COMPLETE)
    )
    asyncio.run(poller.poll_once())

    snapshot = health.snapshot()
    assert snapshot["last_success_at"] is not None
    assert snapshot["consecutive_failures"] == 0
    assert snapshot["total_polls"] == 1
    assert snapshot["total_emails_processed"] == 1

    # And the shape the endpoint returns matches.
    reported = staff_client.get("/api/v1/ops/stats").json()["email"]["poller"]
    assert set(reported) == set(snapshot)


def test_poller_failures_are_visible_and_recover(mailbox, client, monkeypatch):
    health = PollerHealth()
    poller = EmailPoller(health=health)

    async def broken():
        raise OSError("imap unreachable")

    monkeypatch.setattr(mailbox, "fetch_new", broken)
    asyncio.run(poller.poll_once())
    asyncio.run(poller.poll_once())

    assert health.consecutive_failures == 2
    assert health.last_failure_at is not None
    # The type name, not the message - an exception payload could quote mail.
    assert health.last_failure_reason == "OSError"
    assert health.last_success_at is None

    monkeypatch.undo()
    asyncio.run(poller.poll_once())
    assert health.consecutive_failures == 0, "a recovery must clear the failure streak"
    assert health.last_success_at is not None


def test_ops_stats_contains_no_customer_data(client, staff_client):
    client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "private.person@example.com",
            "customer_name": "Private Person",
            "subject": "Withdrawal",
            "body": COMPLETE,
        },
    )

    body = staff_client.get("/api/v1/ops/stats").text

    for leak in (
        "private.person@example.com",
        "Private Person",
        "U-482913",
        "TXN-9f3a12bc",
        "jane.doe@example.com",
    ):
        assert leak not in body, f"operational stats leaked {leak!r}"


def test_ops_stats_contains_no_credentials(client, staff_client, monkeypatch):
    from app.api.routes import ops

    monkeypatch.setattr(ops.settings, "imap_password", "super-secret-value")
    monkeypatch.setattr(ops.settings, "smtp_password", "super-secret-value")

    body = staff_client.get("/api/v1/ops/stats").text
    assert "super-secret-value" not in body
    assert "test-staff-key" not in body
