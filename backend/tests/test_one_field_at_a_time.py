"""The engine must ask for exactly ONE missing required field per message.

Architecture under test (see backend/README.md):
  - ConversationEngine decides WHICH field is missing (schema order - see
    ConversationEngine.advance's `missing_fields=missing[:1]`, `missing`
    itself coming from `_classify_fields` in schema declaration order). It
    never contains per-language branching.
  - RuleBasedAIProvider decides HOW to phrase that one field's question in
    the resolved language (`_FIELD_QUESTION_PHRASES`, keyed by the field's
    internal key - never by inventing/choosing which field to ask about).

`IntakeResult.missing_fields` (a structured API field, i.e. `outcome.missing_fields`
in the engine) intentionally still reports *every* remaining required field -
only the customer-facing `reply_body` is restricted to one field at a time.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

_INTERNAL_KEYS = (
    "user_id",
    "account_email",
    "withdrawal_transaction_id",
    "source_wallet_or_account",
    "transaction_date",
    "deposit_method",
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


def _assert_no_internal_keys_exposed(reply_body: str) -> None:
    for key in _INTERNAL_KEYS:
        assert key not in reply_body, f"internal key {key!r} leaked into reply: {reply_body!r}"


# --------------------------------------------------------------- A, B, C, D


def test_withdrawal_asks_one_field_at_a_time_then_creates_ticket(client):
    addr = "one-at-a-time@example.com"

    # A - all three fields missing: only user_id is asked.
    r1 = send(
        client,
        "I cannot withdraw my money and it has been stuck for two days.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["is_complete"] is False
    assert set(r1["missing_fields"]) == {"user_id", "account_email", "withdrawal_transaction_id"}
    assert "user ID" in r1["reply_body"]
    assert "email" not in r1["reply_body"].lower()
    assert "transaction" not in r1["reply_body"].lower()
    _assert_no_internal_keys_exposed(r1["reply_body"])

    # B - after the user ID, only account_email is asked.
    r2 = send(client, "My user ID is U-482913.", conversation_id=cid, from_addr=addr)
    assert r2["is_complete"] is False
    assert set(r2["missing_fields"]) == {"account_email", "withdrawal_transaction_id"}
    assert "email address" in r2["reply_body"].lower()
    assert "transaction" not in r2["reply_body"].lower()
    _assert_no_internal_keys_exposed(r2["reply_body"])

    # C - after the email, only withdrawal_transaction_id is asked.
    r3 = send(client, "It's jane.doe@example.com.", conversation_id=cid, from_addr=addr)
    assert r3["is_complete"] is False
    assert r3["missing_fields"] == ["withdrawal_transaction_id"]
    assert "transaction" in r3["reply_body"].lower()
    assert "email" not in r3["reply_body"].lower()
    _assert_no_internal_keys_exposed(r3["reply_body"])

    # D - the last field completes the complaint: ticket created, concise
    # confirmation, no internal field names.
    r4 = send(client, "The transaction ID is TXN-9f3a12bc.", conversation_id=cid, from_addr=addr)
    assert r4["is_complete"] is True
    assert r4["ticket_reference"] is not None
    _assert_no_internal_keys_exposed(r4["reply_body"])


# ------------------------------------------------------------------------- E


def test_providing_two_fields_at_once_skips_straight_to_the_remaining_one(client):
    addr = "batch@example.com"
    r1 = send(
        client,
        "My withdrawal never arrived. User ID: U-12345. Account email: test@example.com.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    # Both are extracted in one pass - only the transaction id is still
    # missing, and it's the only thing asked about.
    assert r1["missing_fields"] == ["withdrawal_transaction_id"]
    assert "transaction" in r1["reply_body"].lower()
    assert "user id" not in r1["reply_body"].lower()
    assert "email" not in r1["reply_body"].lower()

    r2 = send(client, "The transaction id is WD-123456.", conversation_id=cid, from_addr=addr)
    assert r2["is_complete"] is True
    assert r2["ticket_reference"] is not None


def test_batch_leaves_earliest_remaining_field_asked_first(client):
    """Providing two non-adjacent fields at once must not shift the ask to
    whichever field happens to come after them - it's always the earliest
    still-missing field in schema order (user_id, account_email,
    source_wallet_or_account, transaction_date, deposit_method)."""
    r = send(
        client,
        "My deposit never arrived. User ID: U-1. Transaction date: 2026-09-01.",
        from_addr="deposit-batch@example.com",
    )
    assert set(r["missing_fields"]) == {
        "account_email",
        "source_wallet_or_account",
        "deposit_method",
    }
    body = r["reply_body"].lower()
    assert "email" in body  # account_email is earliest among the remaining three
    assert "wallet" not in body
    assert "how you made the deposit" not in body


# ---------------------------------------------------------------------- F/G/H


def test_arabic_asks_one_field_at_a_time(client):
    addr = "ar-one@example.com"
    r1 = send(client, "مرحبا، لدي مشكلة في سحب الأموال.", from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["language_code"] == "ar"
    assert "المستخدم" in r1["reply_body"]  # the user-id question
    assert "البريد" not in r1["reply_body"]  # not the email question yet
    _assert_no_internal_keys_exposed(r1["reply_body"])

    r2 = send(
        client,
        "رقم المستخدم الخاص بي هو user id: U-1.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["conversation"]["language_code"] == "ar"
    assert "البريد" in r2["reply_body"]  # now asks for the email
    assert "عملية السحب" not in r2["reply_body"]  # not the transaction-id question yet


def test_ticket_confirmation_stays_in_arabic_even_when_last_answer_is_pure_latin(client):
    """Regression test: a customer answering the final one-field question
    with just the field's value in Latin characters (an ID, a plain email,
    a code) must not flip an otherwise-Arabic conversation's final ticket
    confirmation into English. See RuleBasedAIProvider.detect_language's
    weak/slow-growing confidence for plain Latin text - a short reply that
    is little more than a label + code must not out-vote an established
    non-default conversation language.
    """
    addr = "ar-sticky@example.com"
    r1 = send(
        client,
        "مرحبا، لدي مشكلة في سحب الأموال. طلبت السحب أمس ولكن المال لم يصل إلى حسابي.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["language_code"] == "ar"

    r2 = send(
        client,
        "رقم المستخدم الخاص بي هو user id: U-482913.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["conversation"]["language_code"] == "ar"

    r3 = send(
        client,
        "البريد هو account email: jane.doe@example.com",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r3["conversation"]["language_code"] == "ar"

    # This final message is pure Latin/ASCII - no Arabic wrapper text at all.
    r4 = send(client, "transaction id: TXN-9f3a12bc", conversation_id=cid, from_addr=addr)
    assert r4["is_complete"] is True
    assert r4["ticket_reference"] is not None
    assert r4["conversation"]["language_code"] == "ar"
    assert "شكرًا" in r4["reply_body"]


def test_russian_asks_one_field_at_a_time(client):
    addr = "ru-one@example.com"
    r1 = send(client, "Здравствуйте, у меня проблема с выводом денег.", from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["language_code"] == "ru"
    assert "ID пользователя" in r1["reply_body"]
    assert "email" not in r1["reply_body"]
    _assert_no_internal_keys_exposed(r1["reply_body"])

    # Keep a Cyrillic word alongside the field value so the message still
    # carries a Russian language signal (a purely-Latin reply, like a bare
    # "user id: U-1", would itself detect as English - see
    # RuleBasedAIProvider.detect_language).
    r2 = send(client, "Мой user id: U-1.", conversation_id=cid, from_addr=addr)
    assert r2["conversation"]["language_code"] == "ru"
    assert "email" in r2["reply_body"]
    assert "транзакции вывода" not in r2["reply_body"]


def test_english_asks_one_field_at_a_time(client):
    addr = "en-one@example.com"
    r1 = send(
        client,
        "Hi, I have a problem with my withdrawal. The money hasn't arrived.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["language_code"] == "en"
    assert "user ID" in r1["reply_body"]
    assert "email" not in r1["reply_body"].lower()

    r2 = send(client, "My user ID is U-1.", conversation_id=cid, from_addr=addr)
    assert "email" in r2["reply_body"].lower()
    assert "transaction" not in r2["reply_body"].lower()


# ------------------------------------------------------------------------- I


def test_deposit_fields_asked_one_at_a_time_in_schema_order(client):
    addr = "deposit-one@example.com"
    r1 = send(client, "My deposit never arrived.", from_addr=addr)
    cid = r1["conversation"]["id"]
    assert set(r1["missing_fields"]) == {
        "user_id",
        "account_email",
        "source_wallet_or_account",
        "transaction_date",
        "deposit_method",
    }
    assert "user ID" in r1["reply_body"]

    r2 = send(client, "User ID: U-1.", conversation_id=cid, from_addr=addr)
    assert "email" in r2["reply_body"].lower()

    r3 = send(client, "Account email: bob@example.com.", conversation_id=cid, from_addr=addr)
    assert "wallet" in r3["reply_body"].lower()

    r4 = send(
        client,
        "Source wallet or payment account: my Barclays account.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert "date" in r4["reply_body"].lower()

    r5 = send(client, "Transaction date: 2026-09-05.", conversation_id=cid, from_addr=addr)
    assert "deposit" in r5["reply_body"].lower()

    r6 = send(client, "Deposit method: bank transfer.", conversation_id=cid, from_addr=addr)
    assert r6["is_complete"] is True
    assert r6["ticket_reference"] is not None
    # No deposit-method-specific fields or hard-coded methods were introduced.
    fields = {f["key"] for f in r6["conversation"]["complaint"]["fields"]}
    assert fields == {
        "user_id",
        "account_email",
        "source_wallet_or_account",
        "transaction_date",
        "deposit_method",
        "problem_description",
    }
