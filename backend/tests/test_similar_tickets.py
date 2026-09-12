"""Similar / possibly-duplicate tickets.

These run on the hashing embedder (the default), whose thresholds treat only
near-verbatim wording as "similar" - so the cases below are built from
identity and field overlap plus deliberately close wording, which is what the
policy is about. What is asserted is the *decision*: which signals make a
duplicate, which only make "similar", and that nothing is ever changed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.models.ticket import Ticket
from app.email.base import InboundEmail
from app.services.intake_service import IntakeService

WITHDRAWAL = (
    "My withdrawal never arrived. user id U-{uid}, account email {email}, "
    "transaction {txn}."
)


def open_ticket(client, sender, *, uid="482913", txn="TXN-9f3a12bc", body=None):
    text = body or WITHDRAWAL.format(uid=uid, email=sender, txn=txn)
    response = client.post("/api/v1/inbox", json={"from_addr": sender, "body": text})
    assert response.status_code == 201, response.text
    reference = response.json()["ticket_reference"]
    assert reference, "the message is complete enough to become a ticket"
    return reference


def similar(staff_client, reference):
    response = staff_client.get(f"/api/v1/tickets/{reference}/similar")
    assert response.status_code == 200, response.text
    return response.json()


def by_ref(result):
    return {item["reference"]: item for item in result["items"]}


def test_the_same_customer_reporting_the_same_transaction_is_a_possible_duplicate(
    client, staff_client
):
    first = open_ticket(client, "dup@example.com")
    second = open_ticket(client, "dup@example.com")
    item = by_ref(similar(staff_client, second))[first]
    assert item["relation"] == "possible_duplicate"
    assert item["same_customer"] is True
    assert "withdrawal_transaction_id" in item["matched_fields"]
    assert {"same_customer", "same_type", "same_withdrawal_transaction_id"} <= set(
        item["reasons"]
    )


def test_one_transaction_reported_by_two_senders_is_a_possible_duplicate(client, staff_client):
    first = open_ticket(client, "alice@example.com", uid="111111", txn="TXN-shared01")
    second = open_ticket(client, "bob@example.com", uid="222222", txn="TXN-shared01")
    item = by_ref(similar(staff_client, second))[first]
    assert item["relation"] == "possible_duplicate"
    assert item["same_customer"] is False
    assert item["matched_fields"] == ["withdrawal_transaction_id"]


def test_close_wording_alone_is_only_similar(client, staff_client):
    first = open_ticket(client, "carol@example.com", uid="333333", txn="TXN-aaaa0001")
    second = open_ticket(client, "dave@example.com", uid="444444", txn="TXN-bbbb0002")
    item = by_ref(similar(staff_client, second)).get(first)
    assert item is not None, "same template wording clears the similarity threshold"
    assert item["relation"] == "similar"
    assert "semantic_similarity" in item["reasons"]
    assert item["matched_fields"] == []


def test_unrelated_tickets_are_not_suggested(client, staff_client):
    first = open_ticket(client, "erin@example.com", uid="555555", txn="TXN-cccc0003")
    other = client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "frank@example.com",
            "body": "The mobile app crashes on the settings page every time I open it, "
            "please fix this issue quickly.",
        },
    ).json()
    if other["ticket_reference"]:
        assert other["ticket_reference"] not in by_ref(similar(staff_client, first))


def test_suggestions_never_change_any_ticket(client, staff_client, db_session):
    first = open_ticket(client, "grace@example.com")
    second = open_ticket(client, "grace@example.com")
    before = {t.reference: (t.status, t.type) for t in db_session.scalars(select(Ticket))}
    count = db_session.scalar(select(func.count()).select_from(Ticket))

    similar(staff_client, second)
    similar(staff_client, first)

    db_session.expire_all()
    after = {t.reference: (t.status, t.type) for t in db_session.scalars(select(Ticket))}
    assert after == before
    assert db_session.scalar(select(func.count()).select_from(Ticket)) == count


def test_the_response_carries_no_field_values(client, staff_client):
    open_ticket(client, "heidi@example.com", txn="TXN-secret99")
    second = open_ticket(client, "heidi@example.com", txn="TXN-secret99")
    body = str(similar(staff_client, second))
    assert "TXN-secret99" not in body and "heidi@example.com" not in body


def test_model_and_thresholds_are_reported(client, staff_client):
    reference = open_ticket(client, "ivan@example.com")
    result = similar(staff_client, reference)
    assert result["model_version"].startswith("hashing-char-ngram")
    assert 0 < result["similar_threshold"] < result["duplicate_threshold"] <= 1


def test_unknown_ticket_is_404(staff_client):
    assert staff_client.get("/api/v1/tickets/999999/similar").status_code == 404


def test_the_endpoint_is_staff_only(client):
    reference = open_ticket(client, "judy@example.com")
    assert client.get(f"/api/v1/tickets/{reference}/similar").status_code == 401


async def test_demo_and_real_tickets_are_never_compared(client, staff_client, db_session):
    demo_ref = open_ticket(client, "kim@example.com", txn="TXN-crossover")
    real = await IntakeService(db_session).handle_inbound_email(
        InboundEmail(
            message_id=f"<{uuid.uuid4().hex}@example.com>",
            from_addr="kim@example.com",
            to_addr="complaints@example.com",
            body=WITHDRAWAL.format(uid="482913", email="kim@example.com", txn="TXN-crossover"),
        )
    )
    assert real.ticket_reference
    assert real.ticket_reference not in by_ref(similar(staff_client, demo_ref))
    assert demo_ref not in by_ref(similar(staff_client, real.ticket_reference))
