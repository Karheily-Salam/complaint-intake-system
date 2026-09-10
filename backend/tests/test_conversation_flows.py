"""End-to-end multi-turn conversation tests (API + engine + rule-based provider + DB).

These exercise the guarantees in the project brief: arbitrary ordering, split
messages, no re-asking, merging, invalid-then-correction, late classification,
late deposit-method discovery, and exact collecting -> ticketed transition.
"""

from __future__ import annotations

import itertools

from fastapi.testclient import TestClient

_counter = itertools.count(1)


def send(
    client: TestClient,
    body: str,
    *,
    conversation_id: int | None = None,
    from_addr: str | None = None,
    name: str = "Jane",
    subject: str = "My complaint",
) -> dict:
    payload: dict = {
        "from_addr": from_addr or f"user{next(_counter)}@example.com",
        "body": body,
        "subject": subject,
        "customer_name": name,
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    resp = client.post("/api/v1/inbox", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def fields_of(result: dict) -> dict[str, dict]:
    complaint = result["conversation"]["complaint"] or {}
    return {f["key"]: f for f in complaint.get("fields", [])}


# --------------------------------------------------------------------------- withdrawal


def test_withdrawal_all_info_in_first_email(client):
    result = send(
        client,
        "I cannot withdraw my money and it has been stuck for two days with no error. "
        "User ID: U-482913. "
        "Account email: jane.doe@example.com. "
        "Withdrawal transaction ID: TXN-9f3a12bc.",
    )
    assert result["complaint_type"] == "withdrawal"
    assert result["is_complete"] is True
    assert result["missing_fields"] == []
    assert result["ticket_reference"] is not None

    f = fields_of(result)
    assert f["user_id"]["value"] == "U-482913"
    assert f["account_email"]["value"] == "jane.doe@example.com"
    assert f["withdrawal_transaction_id"]["value"] == "TXN-9f3a12bc"


def test_withdrawal_information_split_across_three_emails(client):
    addr = "split@example.com"
    r1 = send(
        client,
        "I have a problem with a withdrawal that failed yesterday. It keeps failing.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["complaint_type"] == "withdrawal"
    assert r1["is_complete"] is False
    assert set(r1["missing_fields"]) == {
        "user_id",
        "account_email",
        "withdrawal_transaction_id",
    }

    r2 = send(
        client,
        "My user ID is U-5 and my account email is jane.doe@example.com.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["is_complete"] is False
    assert r2["missing_fields"] == ["withdrawal_transaction_id"]

    r3 = send(
        client,
        "The withdrawal transaction ID is TXN-0001.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r3["is_complete"] is True
    assert r3["ticket_reference"] is not None
    f = fields_of(r3)
    assert f["user_id"]["value"] == "U-5"
    assert f["withdrawal_transaction_id"]["value"] == "TXN-0001"


def test_withdrawal_invalid_email_then_correction(client):
    addr = "correction@example.com"
    r1 = send(
        client,
        "My withdrawal is stuck. User ID: U-7. "
        "Account email: jane.doe@example. "
        "Withdrawal transaction ID: TXN-5555.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["is_complete"] is False
    assert "account_email" in r1["invalid_fields"]
    assert "email" in r1["reply_body"].lower()
    assert fields_of(r1)["account_email"]["status"] == "invalid"

    r2 = send(
        client,
        "Sorry, the correct account email is jane.doe@example.com",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["is_complete"] is True
    assert r2["invalid_fields"] == []
    email_field = fields_of(r2)["account_email"]
    assert email_field["value"] == "jane.doe@example.com"
    assert email_field["status"] == "validated"


def test_already_collected_field_is_never_requested_again(client):
    addr = "noduplicate@example.com"
    r1 = send(
        client,
        "Withdrawal problem. User ID: U-5. Account email: jane.doe@example.com. "
        "The payout never arrived.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["missing_fields"] == ["withdrawal_transaction_id"]

    # Restate user_id (same value) plus the missing transaction id.
    r2 = send(
        client,
        "As I said, my user ID is U-5, and the withdrawal transaction ID is TXN-9.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["is_complete"] is True
    f = fields_of(r2)
    assert f["user_id"]["value"] == "U-5"
    assert f["withdrawal_transaction_id"]["value"] == "TXN-9"


def test_unrelated_reply_does_not_overwrite_or_lose_fields(client):
    addr = "nooverwrite@example.com"
    r1 = send(
        client,
        "Withdrawal issue. User ID: U-321. Account email: keep@example.com. "
        "Money never showed up in my bank.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    before = fields_of(r1)

    r2 = send(
        client,
        "Hello? Is anyone there? This is really frustrating.",
        conversation_id=cid,
        from_addr=addr,
    )
    after = fields_of(r2)
    assert after["user_id"]["value"] == before["user_id"]["value"] == "U-321"
    assert after["account_email"]["value"] == "keep@example.com"
    assert r2["is_complete"] is False
    assert r2["missing_fields"] == ["withdrawal_transaction_id"]


# --------------------------------------------------------------------------- deposit
#
# Deposit has ONE flat set of generic fields. `deposit_method` is free text and
# never triggers extra required fields.

_DEPOSIT_KEYS = {
    "user_id",
    "account_email",
    "source_wallet_or_account",
    "transaction_date",
    "deposit_method",
    "problem_description",
}


def test_deposit_all_generic_fields_in_first_email_creates_ticket(client):
    r = send(
        client,
        "My deposit has not arrived. User ID: U-9. Account email: bob@example.com. "
        "Source wallet or payment account: my Barclays current account. "
        "Transaction date: 2026-09-05. Deposit method: bank transfer. "
        "The money left my bank but was never credited.",
    )
    assert r["complaint_type"] == "deposit"
    assert r["is_complete"] is True
    assert r["missing_fields"] == []
    assert r["ticket_reference"] is not None

    f = fields_of(r)
    assert set(f) == _DEPOSIT_KEYS  # exactly the generic fields, nothing extra
    assert f["deposit_method"]["value"] == "bank transfer"
    assert f["transaction_date"]["value"] == "2026-09-05"


def test_deposit_method_is_free_text_and_supplied_later_is_persisted(client):
    addr = "deposit-freetext@example.com"
    r1 = send(
        client,
        "My deposit is missing. User ID: U-20. Account email: liv@example.com. "
        "Source wallet or payment account: my corner shop cash desk. "
        "Transaction date: 2026-09-06. Nothing was credited to my balance.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["is_complete"] is False
    assert r1["missing_fields"] == ["deposit_method"]
    # nothing method-specific is ever requested
    assert set(r1["missing_fields"]).issubset(_DEPOSIT_KEYS)

    r2 = send(
        client,
        "I deposited using my local payment wallet.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["is_complete"] is True
    assert r2["ticket_reference"] is not None
    assert "local payment wallet" in fields_of(r2)["deposit_method"]["value"]
    assert "local payment wallet" in (r2["method_key"] or "")


def test_deposit_unusual_method_does_not_add_required_fields(client):
    r = send(
        client,
        "Deposit problem. User ID: U-30. Account email: obs@example.com. "
        "Source wallet or payment account: regional credit union account. "
        "Transaction date: 2026-09-03. "
        "Deposit method: an obscure local fintech app called ZapPay. "
        "The deposit just vanished and support never replied.",
    )
    assert r["is_complete"] is True
    assert r["missing_fields"] == []
    f = fields_of(r)
    assert set(f) == _DEPOSIT_KEYS
    assert f["deposit_method"]["value"] == "an obscure local fintech app called ZapPay"


def test_deposit_duplicate_information_not_requested_again(client):
    addr = "deposit-dupe@example.com"
    r1 = send(
        client,
        "Deposit issue. User ID: U-40. Account email: dup@example.com. "
        "The funds never arrived in my account.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert set(r1["missing_fields"]) == {
        "source_wallet_or_account",
        "transaction_date",
        "deposit_method",
    }

    r2 = send(
        client,
        "As before, User ID: U-40. "
        "Source wallet or payment account: my Wise account. "
        "Transaction date: 2026-09-02. Deposit method: Wise transfer.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["is_complete"] is True
    assert fields_of(r2)["user_id"]["value"] == "U-40"


def test_deposit_invalid_email_then_correction(client):
    addr = "deposit-correction@example.com"
    r1 = send(
        client,
        "Deposit not credited. User ID: U-50. Account email: sam@broken (typo). "
        "Source wallet or payment account: my checking account. "
        "Transaction date: 2026-09-01. Deposit method: bank transfer. Money gone.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["is_complete"] is False
    assert "account_email" in r1["invalid_fields"]

    r2 = send(
        client,
        "The account email is sam@example.com",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["is_complete"] is True
    assert r2["invalid_fields"] == []
    assert fields_of(r2)["account_email"]["value"] == "sam@example.com"


# --------------------------------------------------------------------------- other


def test_other_clear_complaint_creates_ticket_with_summary(client, staff_client):
    result = send(
        client,
        "Your mobile app keeps logging me out every few minutes and I cannot see my "
        "portfolio balance or open any support chat. This started three days ago.",
    )
    assert result["complaint_type"] == "other"
    assert result["is_complete"] is True
    assert result["ticket_reference"] is not None

    ticket = staff_client.get(f"/api/v1/tickets/{result['ticket_reference']}").json()
    assert len(ticket["concise_description"]) > 10
    assert ticket["structured_data"]["fields"]["problem_description"]


def test_other_vague_complaint_asks_for_clarification(client):
    result = send(client, "hello please help me")
    assert result["is_complete"] is False
    assert result["ticket_reference"] is None
    assert result["awaiting_clarification"] is True
    assert result["reply_body"]


def test_ambiguous_first_message_then_classified_later(client):
    addr = "ambiguous@example.com"
    r1 = send(client, "I have a problem and need help.", from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["complaint_type"] is None
    assert r1["awaiting_clarification"] is True
    assert r1["ticket_reference"] is None

    r2 = send(
        client,
        "It is about a withdrawal. It failed. User ID: U-88. "
        "Account email: eve@example.com. Withdrawal transaction ID: TXN-4242.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["complaint_type"] == "withdrawal"
    assert r2["is_complete"] is True


# --------------------------------------------------------------------------- transcript


def test_full_transcript_is_preserved(client, staff_client):
    addr = "transcript@example.com"
    r1 = send(client, "Withdrawal failed.", from_addr=addr, subject="ticket please")
    cid = r1["conversation"]["id"]
    send(client, "User ID: U-1.", conversation_id=cid, from_addr=addr)
    send(client, "Account email: t@example.com.", conversation_id=cid, from_addr=addr)
    r4 = send(
        client,
        "Withdrawal transaction ID: TXN-77.",
        conversation_id=cid,
        from_addr=addr,
    )

    convo = staff_client.get(f"/api/v1/conversations/{cid}").json()
    directions = [m["direction"] for m in convo["messages"]]
    inbound = [m for m in convo["messages"] if m["direction"] == "inbound"]
    assert len(inbound) == 4
    assert inbound[0]["body"] == "Withdrawal failed."
    assert inbound[-1]["body"] == "Withdrawal transaction ID: TXN-77."
    # each inbound answered by an outbound
    assert directions.count("outbound") == 4
    assert r4["is_complete"] is True
