"""Customer-facing replies must be written in the customer's own language.

Covers the required behaviour end to end through the real API + engine +
RuleBasedAIProvider + DB (no mocking of the AI layer) - see backend/README.md
"Language" section for the documented policy:

  - the AI provider only detects/infers language and drafts text in it;
  - the deterministic engine owns classification, required fields,
    completeness, and conversation state exactly as before;
  - the engine prefers the language of the customer's *latest* message, and
    falls back to the conversation's previously known language when the
    latest message has no reliable language signal (e.g. only digits).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def send(
    client: TestClient,
    body: str,
    *,
    conversation_id: int | None = None,
    from_addr: str,
    name: str = "Jane",
) -> dict:
    payload: dict = {"from_addr": from_addr, "body": body, "customer_name": name}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    resp = client.post("/api/v1/inbox", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------- A/B/C


def test_english_message_gets_english_response(client):
    result = send(
        client,
        "Hi, I have a problem with my withdrawal. The money hasn't arrived.",
        from_addr="en@example.com",
    )
    assert result["complaint_type"] == "withdrawal"
    assert result["conversation"]["language_code"] == "en"
    # One field at a time (see test_one_field_at_a_time.py): the first ask is
    # the schema's first missing field, user_id, and nothing else.
    assert "user ID" in result["reply_body"]
    assert "email" not in result["reply_body"].lower()


def test_russian_message_gets_russian_response(client):
    result = send(
        client,
        "Здравствуйте, у меня проблема с выводом денег. Деньги не поступили.",
        from_addr="ru@example.com",
    )
    assert result["complaint_type"] == "withdrawal"
    assert result["conversation"]["language_code"] == "ru"
    assert "ID пользователя" in result["reply_body"]


def test_arabic_message_gets_arabic_response(client):
    result = send(
        client,
        "مرحبا، لدي مشكلة في سحب الأموال. المال لم يصل.",
        from_addr="ar@example.com",
    )
    assert result["complaint_type"] == "withdrawal"
    assert result["conversation"]["language_code"] == "ar"
    assert "المستخدم" in result["reply_body"]


# ------------------------------------------------------------------------- D


def test_multi_turn_arabic_conversation_stays_arabic(client):
    addr = "ar-multiturn@example.com"
    r1 = send(client, "مرحبا، لدي مشكلة في سحب الأموال.", from_addr=addr)
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["language_code"] == "ar"
    assert "المستخدم" in r1["reply_body"]  # asks for user_id first, in schema order

    r2 = send(
        client,
        "بريدي الإلكتروني المسجل هو jane.doe@example.com وسأرسل باقي التفاصيل لاحقًا.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["conversation"]["language_code"] == "ar"
    # user_id is still the earliest missing field in schema order, so the
    # engine keeps asking for it even though the customer just supplied a
    # later field (account_email) instead.
    assert "المستخدم" in r2["reply_body"]
    # structured extraction is untouched by the language work - the email is
    # still captured verbatim regardless of the surrounding Arabic text.
    complaint = r2["conversation"]["complaint"]
    fields = {f["key"]: f["value"] for f in complaint["fields"]}
    assert fields["account_email"] == "jane.doe@example.com"


# ------------------------------------------------------------------------- E


def test_language_switches_to_latest_message_then_sticks_on_ambiguous_input(client):
    addr = "switcher@example.com"
    r1 = send(
        client,
        "Hi, I have a problem with my withdrawal. The money hasn't arrived.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["conversation"]["language_code"] == "en"

    # Customer switches to Russian - the reply must follow the latest message.
    # This message supplies account_email, but user_id (earlier in schema
    # order) is still missing, so the engine keeps asking for that.
    r2 = send(
        client,
        "У меня есть данные: мой email jane.doe@example.com",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["conversation"]["language_code"] == "ru"
    assert "ID пользователя" in r2["reply_body"]

    # A message with no language signal at all (no letters, no digits) must
    # not reset the conversation to the default language - it keeps the last
    # known one - and extracts nothing, so the same field is asked again.
    r3 = send(client, "...", conversation_id=cid, from_addr=addr)
    assert r3["conversation"]["language_code"] == "ru"
    assert "ID пользователя" in r3["reply_body"]

    # A bare numeric reply to the pending user_id question is now correctly
    # interpreted as the answer (see ConversationState.pending_field) - the
    # conversation moves on to the next field, still in Russian.
    r4 = send(client, "482913", conversation_id=cid, from_addr=addr)
    assert r4["conversation"]["language_code"] == "ru"
    assert "номер транзакции вывода" in r4["reply_body"]


# --------------------------------------------------------- ticket creation still works


def test_arabic_conversation_still_completes_and_creates_a_ticket(client, staff_client):
    """Full non-English happy path: classification, extraction, validation and
    ticket creation are exactly the deterministic engine behaviour as before -
    only the reply's language changed. (The rule-based provider's field
    extractors are pattern-based on English label words, unchanged by this
    work, so the customer here code-switches the three field labels in
    English within an otherwise Arabic message - a real bilingual customer
    could plausibly write this. A real free-form Arabic conversation is the
    Ollama provider's job, since an LLM understands Arabic field labels too.)
    """
    addr = "ar-ticket@example.com"
    r1 = send(
        client,
        "مرحبا، لدي مشكلة في سحب الأموال. لم تصل الأموال بعد.",
        from_addr=addr,
    )
    cid = r1["conversation"]["id"]
    assert r1["complaint_type"] == "withdrawal"
    assert r1["is_complete"] is False
    assert r1["conversation"]["language_code"] == "ar"

    r2 = send(
        client,
        "بيانات حسابي: user id: U-482913 و account email: jane.doe@example.com "
        "و transaction id: TXN-9f3a12bc.",
        conversation_id=cid,
        from_addr=addr,
    )
    assert r2["is_complete"] is True
    assert r2["ticket_reference"] is not None
    assert r2["conversation"]["language_code"] == "ar"
    assert "شكرًا" in r2["reply_body"]

    fields = {f["key"]: f["value"] for f in r2["conversation"]["complaint"]["fields"]}
    assert fields["user_id"] == "U-482913"
    assert fields["account_email"] == "jane.doe@example.com"
    assert fields["withdrawal_transaction_id"] == "TXN-9f3a12bc"

    ticket = staff_client.get(f"/api/v1/tickets/{r2['ticket_reference']}").json()
    assert ticket["structured_data"]["fields"]["user_id"] == "U-482913"
