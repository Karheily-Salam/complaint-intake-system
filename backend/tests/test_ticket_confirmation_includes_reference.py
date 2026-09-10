"""The final ticket-confirmation message must include the actual generated
ticket reference (e.g. "000001") - not omit it, and not an invented one.

Architecture under test (see backend/README.md):
  - ConversationEngine.advance() no longer composes the ACKNOWLEDGE reply
    itself (the ticket, and therefore its reference, does not exist yet at
    that point - the engine performs no I/O).
  - IntakeService creates the ticket first, then calls the new
    ConversationEngine.compose_ticket_confirmation(outcome, customer_name,
    ticket_reference) with the *real* reference from the persisted Ticket
    row, which is threaded into ReplyRequest.ticket_reference. The AI
    provider only phrases that given string into natural text; it never
    generates or invents the reference itself.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def send(
    client: TestClient,
    body: str,
    *,
    conversation_id: int | None = None,
    from_addr: str,
    name: str | None = "Jane",
) -> dict:
    payload: dict = {"from_addr": from_addr, "body": body, "customer_name": name}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    resp = client.post("/api/v1/inbox", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _assert_reference_matches_the_real_ticket(client: TestClient, result: dict) -> None:
    reference = result["ticket_reference"]
    assert reference is not None
    assert reference.isdigit(), f"reference must be numeric-only, got {reference!r}"
    # The reply must contain this exact reference, not just any digit string.
    assert reference in result["reply_body"]
    # And it must be the reference of the ticket actually persisted in the DB
    # - not a value the AI made up that merely looks like one.
    ticket = client.get(f"/api/v1/tickets/{reference}").json()
    assert ticket["reference"] == reference


def test_english_confirmation_includes_the_real_ticket_reference(client):
    addr = "ref-en@example.com"
    r1 = send(
        client,
        "I cannot withdraw my money and it has been stuck for two days.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    send(client, "My user ID is U-482913.", conversation_id=cid, from_addr=addr)
    send(client, "My account email is jane.doe@example.com.", conversation_id=cid, from_addr=addr)
    r4 = send(
        client, "The transaction ID is TXN-9f3a12bc.", conversation_id=cid, from_addr=addr
    )

    assert r4["is_complete"] is True
    assert r4["conversation"]["language_code"] == "en"
    _assert_reference_matches_the_real_ticket(client, r4)


def test_arabic_confirmation_includes_the_real_ticket_reference(client):
    addr = "ref-ar@example.com"
    r1 = send(
        client,
        "مرحبا، لدي مشكلة في سحب الأموال. طلبت السحب أمس ولكن المال لم يصل إلى حسابي.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    send(
        client,
        "رقم المستخدم الخاص بي هو user id: U-482913.",
        conversation_id=cid,
        from_addr=addr,
    )
    send(
        client,
        "البريد هو account email: jane.doe@example.com",
        conversation_id=cid,
        from_addr=addr,
    )
    r4 = send(
        client,
        "رقم العملية هو transaction id: TXN-9f3a12bc",
        conversation_id=cid,
        from_addr=addr,
    )

    assert r4["is_complete"] is True
    assert r4["conversation"]["language_code"] == "ar"
    assert "شكرًا" in r4["reply_body"]
    _assert_reference_matches_the_real_ticket(client, r4)


def test_russian_confirmation_includes_the_real_ticket_reference(client):
    addr = "ref-ru@example.com"
    r1 = send(
        client,
        "Здравствуйте, у меня проблема с выводом денег. Деньги не поступили.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    # Each follow-up keeps a short Cyrillic word alongside the field value -
    # a plausible bilingual reply, and enough for the language to stay
    # confidently Russian (see RuleBasedAIProvider.detect_language).
    send(client, "Мой user id: U-1.", conversation_id=cid, from_addr=addr)
    send(client, "Мой account email: bob@example.com.", conversation_id=cid, from_addr=addr)
    r4 = send(client, "Мой transaction id: TXN-777.", conversation_id=cid, from_addr=addr)

    assert r4["is_complete"] is True
    assert r4["conversation"]["language_code"] == "ru"
    assert "Спасибо" in r4["reply_body"]
    _assert_reference_matches_the_real_ticket(client, r4)


def test_two_tickets_in_the_same_test_get_distinct_references_both_correctly_shown(client):
    """Guards against the reference being hard-coded or stale/cached."""
    r_a = send(
        client,
        "I cannot withdraw my money and it has been stuck for two days. "
        "User ID: U-1. Account email: a@example.com. Transaction ID: TXN-1.",
        from_addr="ref-a@example.com",
    )
    r_b = send(
        client,
        "I cannot withdraw my money and it has been stuck for two days. "
        "User ID: U-2. Account email: b@example.com. Transaction ID: TXN-2.",
        from_addr="ref-b@example.com",
    )
    assert r_a["is_complete"] is True and r_b["is_complete"] is True
    assert r_a["ticket_reference"] != r_b["ticket_reference"]
    assert r_a["ticket_reference"] in r_a["reply_body"]
    assert r_b["ticket_reference"] in r_b["reply_body"]
    assert r_a["ticket_reference"] not in r_b["reply_body"]
    assert r_b["ticket_reference"] not in r_a["reply_body"]
