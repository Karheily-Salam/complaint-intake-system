"""The customer's next message must be interpreted primarily as an answer to
the field the system's previous outbound message asked for - a bare value
with no label at all, or a full natural-language sentence, in any of the
three supported languages - without needing a numeric-only special case and
without breaking the one-field-at-a-time / multilingual / ticket-reference
behaviour already in place.

Architecture under test (see backend/README.md):
  - ConversationEngine.advance sets `outcome.pending_field` from the exact
    same `missing[:1]` selection used for the ASK reply (no duplicate
    field-selection logic) and persists it via IntakeService onto
    `Conversation.pending_field`.
  - The next turn's ConversationState.pending_field is threaded into
    AIProvider.extract(..., pending_field=...); RuleBasedAIProvider only
    uses it as a *fallback* - a field-specific extractor (label, email,
    numeric date, ...) is always tried first, so nothing here changes
    existing extraction results.
  - The engine still decides completeness/ticket-creation entirely from the
    deterministic missing/invalid computation - the AI only supplies values.
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


def _fields(result: dict) -> dict[str, str]:
    return {f["key"]: f["value"] for f in result["conversation"]["complaint"]["fields"]}


_WITHDRAWAL_OPENER_AR = (
    "مرحبا، لدي مشكلة في سحب الأموال. طلبت السحب أمس ولكن المال لم يصل إلى حسابي."
)


# --------------------------------------------------------------------- A, B


def test_a_numeric_only_user_id_is_stored(client):
    addr = "a-numeric-user-id@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["pending_field"] == "user_id"

    r2 = send(client, "583921", conversation_id=cid, from_addr=addr)
    assert _fields(r2)["user_id"] == "583921"
    assert "user_id" not in r2["missing_fields"]
    assert r2["conversation"]["pending_field"] == "account_email"


def test_b_sentence_containing_user_id_is_stored(client):
    addr = "b-sentence-user-id@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]

    r2 = send(
        client, "رقم المستخدم الخاص بي هو 583921", conversation_id=cid, from_addr=addr
    )
    assert _fields(r2)["user_id"] == "583921"
    assert r2["conversation"]["pending_field"] == "account_email"


# --------------------------------------------------------------------- C, D


def _advance_to_transaction_id(client: TestClient, addr: str) -> tuple[int, dict]:
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    send(client, "583921", conversation_id=cid, from_addr=addr)
    r3 = send(
        client,
        "البريد المرتبط بحسابي هو customer@example.com",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r3["conversation"]["pending_field"] == "withdrawal_transaction_id"
    return cid, r3


def test_c_numeric_only_transaction_id_is_stored(client):
    cid, _ = _advance_to_transaction_id(client, "c-numeric-txn@example.com")
    r4 = send(client, "WD-784512963", conversation_id=cid, from_addr="c-numeric-txn@example.com")
    assert _fields(r4)["withdrawal_transaction_id"] == "WD-784512963"
    assert r4["is_complete"] is True
    assert r4["ticket_reference"] is not None


def test_d_sentence_containing_transaction_id_is_stored(client):
    addr = "d-sentence-txn@example.com"
    cid, _ = _advance_to_transaction_id(client, addr)
    r4 = send(client, "رقم العملية هو WD-784512963", conversation_id=cid, from_addr=addr)
    assert _fields(r4)["withdrawal_transaction_id"] == "WD-784512963"
    assert r4["is_complete"] is True
    assert r4["ticket_reference"] is not None


# ------------------------------------------------------------------------- E


def test_e_email_only_response_is_stored(client):
    addr = "e-email-only@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    send(client, "583921", conversation_id=cid, from_addr=addr)

    r3 = send(client, "customer@example.com", conversation_id=cid, from_addr=addr)
    assert _fields(r3)["account_email"] == "customer@example.com"
    assert r3["conversation"]["pending_field"] == "withdrawal_transaction_id"


# ------------------------------------------------------------------------- F


def test_f_invalid_response_keeps_field_missing_and_reasks_same_field(client):
    addr = "f-invalid@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["pending_field"] == "user_id"

    r2 = send(client, "hello", conversation_id=cid, from_addr=addr)
    assert "user_id" not in _fields(r2)
    assert "user_id" in r2["missing_fields"]
    assert r2["conversation"]["pending_field"] == "user_id"
    assert "المستخدم" in r2["reply_body"]  # still (again) asking for the user id, in Arabic

    r3 = send(client, "مرحبا", conversation_id=cid, from_addr=addr)
    assert "user_id" not in _fields(r3)
    assert r3["conversation"]["pending_field"] == "user_id"


# ------------------------------------------------------------------------- G


def test_g_multi_field_response_extracts_beyond_the_pending_field(client):
    addr = "g-multi-field@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["pending_field"] == "user_id"

    r2 = send(
        client,
        "583921 that's my ID, and the transaction id is WD-784512963",
        conversation_id=cid,
        from_addr=addr,
    )
    fields = _fields(r2)
    assert fields["user_id"] == "583921"
    assert fields["withdrawal_transaction_id"] == "WD-784512963"
    assert r2["missing_fields"] == ["account_email"]
    assert r2["conversation"]["pending_field"] == "account_email"


# --------------------------------------------------------------------- H/I/J


def test_h_arabic_complete_multi_turn_flow(client):
    addr = "h-arabic-flow@example.com"
    r1 = send(client, _WITHDRAWAL_OPENER_AR, from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["complaint_type"] == "withdrawal"
    assert r1["conversation"]["language_code"] == "ar"

    r2 = send(client, "583921", conversation_id=cid, from_addr=addr)
    assert _fields(r2)["user_id"] == "583921"
    assert r2["conversation"]["language_code"] == "ar"

    r3 = send(
        client,
        "البريد المرتبط بحسابي هو customer@example.com",
        conversation_id=cid,
        from_addr=addr,
    )
    assert _fields(r3)["account_email"] == "customer@example.com"
    assert r3["conversation"]["language_code"] == "ar"

    r4 = send(client, "رقم العملية هو WD-784512963", conversation_id=cid, from_addr=addr)
    assert _fields(r4)["withdrawal_transaction_id"] == "WD-784512963"
    assert r4["is_complete"] is True
    assert r4["conversation"]["language_code"] == "ar"
    ref = r4["ticket_reference"]
    assert ref is not None
    assert ref in r4["reply_body"]


def test_i_russian_complete_multi_turn_flow(client):
    addr = "i-russian-flow@example.com"
    r1 = send(
        client,
        "Здравствуйте, у меня проблема с выводом денег. Деньги не поступили.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["complaint_type"] == "withdrawal"
    assert r1["conversation"]["language_code"] == "ru"

    r2 = send(client, "Мой ID пользователя — 583921", conversation_id=cid, from_addr=addr)
    assert _fields(r2)["user_id"] == "583921"
    assert r2["conversation"]["language_code"] == "ru"

    r3 = send(
        client, "Мой email аккаунта — customer@example.com", conversation_id=cid, from_addr=addr
    )
    assert _fields(r3)["account_email"] == "customer@example.com"
    assert r3["conversation"]["language_code"] == "ru"

    r4 = send(client, "Номер операции — WD-784512963", conversation_id=cid, from_addr=addr)
    assert _fields(r4)["withdrawal_transaction_id"] == "WD-784512963"
    assert r4["is_complete"] is True
    assert r4["conversation"]["language_code"] == "ru"
    ref = r4["ticket_reference"]
    assert ref is not None
    assert ref in r4["reply_body"]


def test_j_english_complete_multi_turn_flow_with_bare_answers(client):
    """Bare answers (no "my user id is" label at all) - previously only the
    labelled form worked in English too; this is the same new mechanism,
    not something special-cased for Arabic/Russian."""
    addr = "j-english-flow@example.com"
    r1 = send(
        client,
        "I cannot withdraw my money and it has been stuck for two days.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["language_code"] == "en"

    r2 = send(client, "583921", conversation_id=cid, from_addr=addr)
    assert _fields(r2)["user_id"] == "583921"

    r3 = send(client, "customer@example.com", conversation_id=cid, from_addr=addr)
    assert _fields(r3)["account_email"] == "customer@example.com"

    r4 = send(client, "WD-784512963", conversation_id=cid, from_addr=addr)
    assert _fields(r4)["withdrawal_transaction_id"] == "WD-784512963"
    assert r4["is_complete"] is True
    ref = r4["ticket_reference"]
    assert ref is not None
    assert ref in r4["reply_body"]


# ------------------------------------------------------------------------- K


def test_k_deposit_contextual_replies_for_all_generic_fields(client):
    addr = "k-deposit-flow@example.com"
    r1 = send(client, "My deposit never arrived.", from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["complaint_type"] == "deposit"
    assert r1["conversation"]["pending_field"] == "user_id"

    r2 = send(client, "583921", conversation_id=cid, from_addr=addr)
    assert _fields(r2)["user_id"] == "583921"
    assert r2["conversation"]["pending_field"] == "account_email"

    r3 = send(client, "customer@example.com", conversation_id=cid, from_addr=addr)
    assert _fields(r3)["account_email"] == "customer@example.com"
    assert r3["conversation"]["pending_field"] == "source_wallet_or_account"

    r4 = send(client, "my Barclays account", conversation_id=cid, from_addr=addr)
    assert _fields(r4)["source_wallet_or_account"] == "my Barclays account"
    assert r4["conversation"]["pending_field"] == "transaction_date"

    r5 = send(client, "8 september", conversation_id=cid, from_addr=addr)
    assert _fields(r5)["transaction_date"].endswith("-09-08")
    assert r5["conversation"]["pending_field"] == "deposit_method"

    r6 = send(client, "I used my local payment wallet", conversation_id=cid, from_addr=addr)
    fields = _fields(r6)
    assert fields["deposit_method"] == "I used my local payment wallet"
    assert r6["is_complete"] is True
    assert r6["ticket_reference"] is not None


# ------------------------------------------------------------------------- L


def test_l_deposit_method_remains_arbitrary_free_text(client):
    addr = "l-deposit-method@example.com"
    r1 = send(
        client,
        "مرحبا، لدي مشكلة في عملية الإيداع. المبلغ لم يصل.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["complaint_type"] == "deposit"

    send(client, "583921", conversation_id=cid, from_addr=addr)
    send(client, "customer@example.com", conversation_id=cid, from_addr=addr)
    send(client, "حسابي في بنك الراجحي", conversation_id=cid, from_addr=addr)
    r5 = send(client, "8 سبتمبر", conversation_id=cid, from_addr=addr)
    assert r5["conversation"]["pending_field"] == "deposit_method"

    r6 = send(
        client, "استخدمت محفظتي المحلية للدفع", conversation_id=cid, from_addr=addr
    )
    fields = _fields(r6)
    assert fields["deposit_method"] == "استخدمت محفظتي المحلية للدفع"
    assert r6["is_complete"] is True
    # No predefined category was ever introduced - just this exact free text.
    forbidden = {"bank_transfer", "crypto", "card", "e_wallet"}
    assert forbidden.isdisjoint(fields.keys())
    assert forbidden.isdisjoint({fields["deposit_method"]})
