"""Regression tests for a real workflow bug in the `pending_field` mechanism:

An invalid answer to the field the customer was just asked about must keep
the conversation pending on THAT SAME field - never silently advance to a
different, merely-missing field later in schema order.

Previously `ConversationEngine.advance` computed `outcome.pending_field`
from `missing[0]` alone, which completely ignored the `invalid` list. So if
the customer answered a valid `user_id` then an invalid `account_email`, the
engine would set `pending_field` to `withdrawal_transaction_id` (the next
merely-missing field) instead of staying on `account_email` - and the reply
would ask for the transaction id instead of asking the customer to correct
their email.

Engine-unit-level coverage (using a scripted AI provider to construct exact
invalid-value scenarios that are hard to trigger through the real regex
extractor, since none of the withdrawal string fields have a validation
pattern strict enough to fail on a normally-extracted token) lives in
test_engine_merge.py. This file covers the same fix end to end through the
real API + engine + RuleBasedAIProvider + DB.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

_WITHDRAWAL_OPENER_AR = (
    "مرحبا، لدي مشكلة في سحب الأموال. طلبت السحب أمس ولكن المال لم يصل إلى حسابي."
)


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


def _rows(result: dict) -> dict[str, dict]:
    """Every persisted field row (value + status), keyed by field key."""
    return {f["key"]: f for f in result["conversation"]["complaint"]["fields"]}


def _valid_values(result: dict) -> dict[str, str]:
    """Only fields whose *business state* is resolved (validated/confirmed).

    An invalid value is legitimately persisted for diagnostic display (see
    ``_rows``) - the raw submitted text is kept alongside its "invalid"
    status - but it must never count as resolved here; completeness is
    driven by status, not by value presence.
    """
    return {
        key: row["value"]
        for key, row in _rows(result).items()
        if row["status"] in ("validated", "confirmed")
    }


# ------------------------------------------------------------------------- A


def test_a_withdrawal_exact_reported_bug_full_flow(client):
    """The exact scenario reported: ask user_id -> "111" -> ask account_email
    -> invalid email -> must ask account_email AGAIN, not the transaction id
    -> valid email -> ask transaction id -> valid transaction id -> ticket.
    """
    addr = "a-reported-bug@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["pending_field"] == "user_id"

    r2 = send(client, "111", conversation_id=cid, from_addr=addr)
    assert _valid_values(r2)["user_id"] == "111"
    assert r2["conversation"]["pending_field"] == "account_email"

    r3 = send(client, "سيلبمنتى@سيل", conversation_id=cid, from_addr=addr)
    assert "account_email" not in _valid_values(r3)
    # The invalid text is stored for diagnostics, marked "invalid" - not
    # silently dropped, but also not treated as a resolved value.
    assert _rows(r3)["account_email"]["value"] == "سيلبمنتى@سيل"
    assert _rows(r3)["account_email"]["status"] == "invalid"
    assert r3["conversation"]["pending_field"] == "account_email"  # NOT withdrawal_transaction_id
    assert r3["conversation"]["status"] == "collecting_info"
    assert r3["invalid_fields"] == ["account_email"]
    assert "withdrawal_transaction_id" not in r3["reply_body"]
    assert r3["is_complete"] is False

    r4 = send(
        client,
        "البريد المرتبط بحسابي هو customer@example.com",
        conversation_id=cid,
        from_addr=addr,
    )
    assert _valid_values(r4)["account_email"] == "customer@example.com"
    assert r4["conversation"]["pending_field"] == "withdrawal_transaction_id"

    r5 = send(client, "رقم العملية هو WD-784512963", conversation_id=cid, from_addr=addr)
    assert _valid_values(r5)["withdrawal_transaction_id"] == "WD-784512963"
    assert r5["is_complete"] is True
    assert r5["ticket_reference"] is not None
    assert r5["ticket_reference"] in r5["reply_body"]


# ------------------------------------------------------------------------- C


def test_c_invalid_transaction_id_does_not_advance_or_create_ticket(client):
    addr = "c-invalid-txn@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    send(client, "111", conversation_id=cid, from_addr=addr)
    send(
        client,
        "البريد المرتبط بحسابي هو customer@example.com",
        conversation_id=cid,
        from_addr=addr,
    )

    # "12" is a plausible bare answer but fails withdrawal_transaction_id's
    # own min_length=4 validation - a genuine validation rejection, not an
    # extraction miss.
    r4 = send(client, "12", conversation_id=cid, from_addr=addr)
    assert "withdrawal_transaction_id" not in _valid_values(r4)
    assert _rows(r4)["withdrawal_transaction_id"]["status"] == "invalid"
    assert r4["conversation"]["pending_field"] == "withdrawal_transaction_id"
    assert r4["invalid_fields"] == ["withdrawal_transaction_id"]
    assert r4["is_complete"] is False
    assert r4["ticket_reference"] is None

    r5 = send(client, "WD-784512963", conversation_id=cid, from_addr=addr)
    assert _valid_values(r5)["withdrawal_transaction_id"] == "WD-784512963"
    assert r5["is_complete"] is True
    assert r5["ticket_reference"] is not None


# ------------------------------------------------------------------------- D


def test_d_deposit_invalid_transaction_date_does_not_advance_to_deposit_method(client):
    addr = "d-deposit-invalid-date@example.com"
    r1 = send(client, "My deposit never arrived.", from_addr=addr)
    cid = r1["conversation"]["id"]
    send(client, "111", conversation_id=cid, from_addr=addr)
    send(client, "customer@example.com", conversation_id=cid, from_addr=addr)
    r3 = send(client, "my Barclays account", conversation_id=cid, from_addr=addr)
    assert r3["conversation"]["pending_field"] == "transaction_date"

    # Syntactically a date, but month 13 does not exist - validate_field's
    # date parser must reject it.
    r4 = send(client, "2026-13-40", conversation_id=cid, from_addr=addr)
    assert "transaction_date" not in _valid_values(r4)
    assert _rows(r4)["transaction_date"]["status"] == "invalid"
    assert r4["conversation"]["pending_field"] == "transaction_date"
    assert r4["invalid_fields"] == ["transaction_date"]
    assert "deposit" not in r4["reply_body"].lower()  # not asking about deposit_method yet

    r5 = send(client, "2026-09-05", conversation_id=cid, from_addr=addr)
    assert _valid_values(r5)["transaction_date"] == "2026-09-05"
    assert r5["conversation"]["pending_field"] == "deposit_method"

    r6 = send(client, "I used my local payment wallet", conversation_id=cid, from_addr=addr)
    assert _valid_values(r6)["deposit_method"] == "I used my local payment wallet"
    assert r6["is_complete"] is True
    assert r6["ticket_reference"] is not None
    # No predefined deposit-method category was ever introduced.
    assert set(_rows(r6)).isdisjoint({"bank_transfer", "crypto", "card", "e_wallet"})


# ------------------------------------------------------------------------- E


def test_e_english_invalid_email_reasks_same_field(client):
    addr = "e-english-invalid@example.com"
    r1 = send(
        client,
        "I cannot withdraw my money and it has been stuck for two days.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    send(client, "111", conversation_id=cid, from_addr=addr)

    r3 = send(client, "test@invalid", conversation_id=cid, from_addr=addr)
    assert "account_email" not in _valid_values(r3)
    assert _rows(r3)["account_email"]["status"] == "invalid"
    assert r3["conversation"]["pending_field"] == "account_email"
    assert r3["conversation"]["language_code"] == "en"
    assert r3["invalid_fields"] == ["account_email"]
    assert "email" in r3["reply_body"].lower()

    r4 = send(client, "customer@example.com", conversation_id=cid, from_addr=addr)
    assert _valid_values(r4)["account_email"] == "customer@example.com"
    assert r4["conversation"]["pending_field"] == "withdrawal_transaction_id"


def test_e_russian_invalid_email_reasks_same_field(client):
    addr = "e-russian-invalid@example.com"
    r1 = send(
        client,
        "Здравствуйте, у меня проблема с выводом денег. Деньги не поступили.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    send(client, "Мой ID пользователя — 111", conversation_id=cid, from_addr=addr)

    r3 = send(client, "Мой email это test@invalid", conversation_id=cid, from_addr=addr)
    assert "account_email" not in _valid_values(r3)
    assert _rows(r3)["account_email"]["status"] == "invalid"
    assert r3["conversation"]["pending_field"] == "account_email"
    assert r3["conversation"]["language_code"] == "ru"
    assert r3["invalid_fields"] == ["account_email"]

    r4 = send(
        client, "Мой email аккаунта — customer@example.com", conversation_id=cid, from_addr=addr
    )
    assert _valid_values(r4)["account_email"] == "customer@example.com"
    assert r4["conversation"]["pending_field"] == "withdrawal_transaction_id"


# ------------------------------------------------------------------------- G


def test_g_multi_field_answer_with_one_invalid_still_prioritizes_it(client):
    """The customer answers the pending field (invalidly) and also supplies
    a different, valid field in the same message: both are extracted and
    validated, but the reply must still ask about the invalid pending field,
    not the field that just became valid."""
    addr = "g-multi-with-invalid@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    send(client, "111", conversation_id=cid, from_addr=addr)
    assert r1["conversation"]["pending_field"] == "user_id"

    r3 = send(
        client,
        "بريدي هو سيلبمنتى@سيل and the transaction id is WD-1234567",
        conversation_id=cid,
        from_addr=addr,
    )
    valid = _valid_values(r3)
    assert "account_email" not in valid
    assert _rows(r3)["account_email"]["status"] == "invalid"
    assert valid["withdrawal_transaction_id"] == "WD-1234567"
    assert r3["conversation"]["pending_field"] == "account_email"
    assert r3["invalid_fields"] == ["account_email"]
    assert r3["missing_fields"] == []
    assert r3["is_complete"] is False

    r4 = send(client, "customer@example.com", conversation_id=cid, from_addr=addr)
    assert _valid_values(r4)["account_email"] == "customer@example.com"
    assert r4["is_complete"] is True
    assert r4["ticket_reference"] is not None
