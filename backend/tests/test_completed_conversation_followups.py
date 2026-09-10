"""A finished thread must not keep confirming itself.

Customers reply to completed threads - "thanks", a question, occasionally a
corrected value. Only the last of those is worth another email. The decision
is made from persisted complaint state (did extraction actually change a
field this turn), never from message counts or the text of the reply.
"""

from __future__ import annotations

import pytest

from app.core.config import settings

COMPLETE_WITHDRAWAL = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)
ADDRESS = "followup@example.com"


@pytest.fixture()
def mailbox(client):
    from app.email.factory import get_email_provider

    return get_email_provider()


def send(client, body: str, conversation_id: int | None = None) -> dict:
    payload: dict = {"from_addr": ADDRESS, "body": body, "subject": "Withdrawal"}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    resp = client.post("/api/v1/inbox", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def customer_emails(mailbox) -> list:
    return [e for e in mailbox.sent_bodies if e.to_addr == ADDRESS]


def support_emails(mailbox) -> list:
    return [e for e in mailbox.sent_bodies if e.to_addr == settings.support_inbox_address]


def test_completion_sends_exactly_one_confirmation(client, mailbox):
    result = send(client, COMPLETE_WITHDRAWAL)

    assert result["is_complete"] is True
    reference = result["ticket_reference"]
    confirmations = [e for e in customer_emails(mailbox) if reference in e.body]
    assert len(confirmations) == 1
    assert len(support_emails(mailbox)) == 1


def test_thanks_after_completion_sends_no_second_confirmation(client, mailbox):
    first = send(client, COMPLETE_WITHDRAWAL)
    cid = first["conversation"]["id"]
    after_completion = len(mailbox.sent_bodies)

    followup = send(client, "Thank you very much!", conversation_id=cid)

    assert followup["ticket_reference"] == first["ticket_reference"]
    assert followup["reply_body"] is None, "a bare thank-you must not be answered"
    assert len(mailbox.sent_bodies) == after_completion, "no further email may be sent"


def test_repeated_irrelevant_replies_send_nothing_further(client, mailbox):
    first = send(client, COMPLETE_WITHDRAWAL)
    cid = first["conversation"]["id"]
    after_completion = len(mailbox.sent_bodies)

    for body in ("Thanks!", "Ok.", "Great, appreciated.", "Thanks again"):
        send(client, body, conversation_id=cid)

    assert len(mailbox.sent_bodies) == after_completion
    # And support is not re-notified about a ticket it already has.
    assert len(support_emails(mailbox)) == 1


def test_a_real_correction_after_completion_does_get_answered(client, mailbox, monkeypatch):
    """A changed field is legitimate new information, so we reply again.

    The correction has to come from a provider that can actually detect one.
    The default rule-based provider deliberately does not re-extract a field
    that is already valid - it says so in its own docstring, because spotting
    "actually it was X" needs real language understanding, which is what the
    Ollama provider is for. Substituting an extraction here keeps the test
    about the thing under test: IntakeService's decision to answer a genuine
    change while ignoring chatter.
    """
    from app.ai.base import ExtractedField, ExtractionResult
    from app.ai.factory import get_ai_provider

    first = send(client, COMPLETE_WITHDRAWAL)
    cid = first["conversation"]["id"]
    reference = first["ticket_reference"]
    after_completion = len(mailbox.sent_bodies)

    provider = get_ai_provider()
    original_extract = provider.extract

    async def extract_with_correction(message, specs, known=None, pending_field=None):
        if "TXN-000111aa" in message:
            return ExtractionResult(
                fields=[
                    ExtractedField(
                        key="withdrawal_transaction_id", value="TXN-000111aa", confidence=0.9
                    )
                ]
            )
        return await original_extract(message, specs, known, pending_field)

    monkeypatch.setattr(provider, "extract", extract_with_correction)

    corrected = send(
        client,
        "Sorry, the correct transaction id is TXN-000111aa.",
        conversation_id=cid,
    )

    assert corrected["ticket_reference"] == reference, "same ticket, not a second one"
    assert corrected["reply_body"] is not None
    assert len(mailbox.sent_bodies) == after_completion + 1

    # The ticket snapshot reflects the correction...
    fields = {
        f["key"]: f["value"] for f in corrected["conversation"]["complaint"]["fields"]
    }
    assert fields["withdrawal_transaction_id"] == "TXN-000111aa"
    # ...and support was still notified exactly once, at creation.
    assert len(support_emails(mailbox)) == 1


def test_restating_the_same_value_is_not_a_change(client, mailbox):
    """Guards against 'any message mentioning a field' counting as a change."""
    first = send(client, COMPLETE_WITHDRAWAL)
    cid = first["conversation"]["id"]
    after_completion = len(mailbox.sent_bodies)

    send(client, "Just confirming: transaction TXN-9f3a12bc.", conversation_id=cid)

    assert len(mailbox.sent_bodies) == after_completion


async def test_duplicate_message_id_after_completion_stays_idempotent(client, db_session):
    """The completion change must not weaken idempotency."""
    from sqlalchemy import func, select

    from app.core.database import SessionLocal
    from app.db.models.conversation import Conversation
    from app.email.factory import get_email_provider
    from app.services.email_poller import EmailPoller

    mailbox = get_email_provider()
    poller = EmailPoller(session_factory=SessionLocal)
    email = mailbox.make_inbound(
        from_addr="dupe-complete@example.com",
        subject="Withdrawal",
        body=COMPLETE_WITHDRAWAL,
    )

    mailbox.deliver(email)
    assert await poller.poll_once() == 1
    sent_after_first = len(mailbox.sent_bodies)

    mailbox.deliver(email)  # redelivered
    await poller.poll_once()

    assert len(mailbox.sent_bodies) == sent_after_first, "replay must send nothing"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.customer.has(email="dupe-complete@example.com"))
        )
        == 1
    )
