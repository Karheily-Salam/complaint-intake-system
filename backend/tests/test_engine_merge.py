"""Unit tests for the engine's deterministic merge / correction behaviour.

These use a scripted AI provider so we can drive exact extraction results and
assert the engine - not the provider - owns the state transitions.
"""

from __future__ import annotations

import pytest

from app.ai.base import (
    AIProvider,
    Classification,
    ExtractedField,
    ExtractionResult,
    LanguageDetection,
    ReplyDraft,
    ReplyRequest,
)
from app.conversation.engine import ConversationEngine
from app.conversation.state import CollectedField, ConversationState
from app.domain.complaint_schemas.registry import get_registry
from app.domain.enums import FieldStatus

pytestmark = pytest.mark.asyncio


class ScriptedAI(AIProvider):
    name = "scripted"

    def __init__(self) -> None:
        self.classification = Classification(type="withdrawal", confidence=0.9)
        self.extractions: list[ExtractionResult] = []
        self.summary = "The customer's withdrawal failed repeatedly over two days with no error."

    async def classify(self, message, options):
        return self.classification

    async def extract(self, message, specs, known=None, pending_field=None):
        return self.extractions.pop(0) if self.extractions else ExtractionResult()

    async def summarize(self, transcript):
        return self.summary

    async def detect_language(self, message):
        return LanguageDetection(code="en", confidence=1.0)

    async def compose_reply(self, request: ReplyRequest):
        return ReplyDraft(body=f"[{request.kind.value}]")


def engine() -> ConversationEngine:
    return ConversationEngine(ScriptedAI(), get_registry())


def _all_valid_withdrawal() -> list[CollectedField]:
    return [
        CollectedField("user_id", "U-1", FieldStatus.VALIDATED),
        CollectedField("account_email", "a@example.com", FieldStatus.VALIDATED),
        CollectedField("withdrawal_transaction_id", "TXN-1", FieldStatus.VALIDATED),
        CollectedField("problem_description", "Withdrawal stuck for days.", FieldStatus.VALIDATED),
    ]


async def test_absent_extraction_never_clears_existing_fields():
    eng = engine()
    eng._ai.extractions = [ExtractionResult(fields=[])]  # nothing new
    state = ConversationState(
        latest_message="any update?",
        complaint_type="withdrawal",
        collected=_all_valid_withdrawal(),
    )
    outcome = await eng.advance(state)
    assert outcome.is_complete is True
    values = {f.key: f.value for f in outcome.fields}
    assert values["user_id"] == "U-1"
    assert values["withdrawal_transaction_id"] == "TXN-1"
    assert all(not f.changed for f in outcome.fields)


async def test_invalid_value_is_corrected_by_later_message():
    eng = engine()
    eng._ai.extractions = [
        ExtractionResult(fields=[ExtractedField(key="account_email", value="not-an-email")])
    ]
    state = ConversationState(
        latest_message="my email is not-an-email",
        complaint_type="withdrawal",
        collected=[
            CollectedField("user_id", "U-1", FieldStatus.VALIDATED),
            CollectedField("withdrawal_transaction_id", "TXN-1", FieldStatus.VALIDATED),
            CollectedField("problem_description", "stuck", FieldStatus.VALIDATED),
        ],
    )
    outcome = await eng.advance(state)
    assert outcome.is_complete is False
    assert [s.key for s in outcome.invalid_fields] == ["account_email"]

    eng._ai.extractions = [
        ExtractionResult(fields=[ExtractedField(key="account_email", value="real@example.com")])
    ]
    state2 = ConversationState(
        latest_message="sorry it is real@example.com",
        complaint_type="withdrawal",
        collected=[
            CollectedField("user_id", "U-1", FieldStatus.VALIDATED),
            CollectedField("withdrawal_transaction_id", "TXN-1", FieldStatus.VALIDATED),
            CollectedField("problem_description", "stuck", FieldStatus.VALIDATED),
            CollectedField("account_email", "not-an-email", FieldStatus.INVALID),
        ],
    )
    outcome2 = await eng.advance(state2)
    assert outcome2.is_complete is True
    email = next(f for f in outcome2.fields if f.key == "account_email")
    assert email.value == "real@example.com"
    assert email.status == FieldStatus.VALIDATED


async def test_invalid_pending_field_is_not_overridden_by_a_later_missing_field():
    """Regression: an invalid answer to the field the customer was just
    asked about must keep the conversation pending on that field, never
    jump ahead to a different, merely-missing field later in schema order
    (previously `pending_field` was computed from `missing[0]` alone, which
    ignored invalid fields entirely)."""
    eng = engine()
    eng._ai.extractions = [
        ExtractionResult(fields=[ExtractedField(key="account_email", value="not-an-email")])
    ]
    state = ConversationState(
        latest_message="not-an-email",
        complaint_type="withdrawal",
        collected=[
            CollectedField("user_id", "U-1", FieldStatus.VALIDATED),
            CollectedField("problem_description", "stuck", FieldStatus.VALIDATED),
        ],
        pending_field="account_email",
    )
    outcome = await eng.advance(state)
    assert outcome.is_complete is False
    assert outcome.pending_field == "account_email"
    assert [s.key for s in outcome.invalid_fields] == ["account_email"]
    # withdrawal_transaction_id is genuinely still missing too, and the API
    # still reports it - it just must not become the pending field.
    assert [s.key for s in outcome.missing_fields] == ["withdrawal_transaction_id"]


async def test_earlier_missing_field_still_takes_priority_over_a_later_invalid_one():
    """Not "invalid always wins" - schema order decides. A field earlier in
    declaration order that is merely missing must still be asked about
    before a later field that happens to be invalid."""
    eng = engine()
    eng._ai.extractions = [ExtractionResult(fields=[])]
    state = ConversationState(
        latest_message="anything",
        complaint_type="withdrawal",
        collected=[
            CollectedField("account_email", "not-an-email", FieldStatus.INVALID),
            CollectedField("problem_description", "stuck", FieldStatus.VALIDATED),
        ],
        pending_field="account_email",
    )
    outcome = await eng.advance(state)
    assert outcome.is_complete is False
    # user_id comes before account_email in the withdrawal schema.
    assert outcome.pending_field == "user_id"


async def test_invalid_user_id_does_not_advance_to_account_email():
    """Letter B: the same priority rule for user_id specifically - not an
    account_email-only special case."""
    eng = engine()
    eng._ai.extractions = [ExtractionResult(fields=[ExtractedField(key="user_id", value="x")])]
    state = ConversationState(
        latest_message="x",
        complaint_type="withdrawal",
        collected=[CollectedField("problem_description", "stuck", FieldStatus.VALIDATED)],
        pending_field="user_id",
    )
    outcome = await eng.advance(state)
    assert outcome.is_complete is False
    assert outcome.pending_field == "user_id"
    assert [s.key for s in outcome.invalid_fields] == ["user_id"]
    assert "account_email" not in [f.key for f in outcome.fields if f.is_present]


async def test_invalid_withdrawal_transaction_id_does_not_create_a_ticket():
    """Letter C: same priority rule for withdrawal_transaction_id."""
    eng = engine()
    eng._ai.extractions = [
        ExtractionResult(fields=[ExtractedField(key="withdrawal_transaction_id", value="ab")])
    ]
    state = ConversationState(
        latest_message="ab",
        complaint_type="withdrawal",
        collected=[
            CollectedField("user_id", "U-1", FieldStatus.VALIDATED),
            CollectedField("account_email", "a@example.com", FieldStatus.VALIDATED),
            CollectedField("problem_description", "stuck", FieldStatus.VALIDATED),
        ],
        pending_field="withdrawal_transaction_id",
    )
    outcome = await eng.advance(state)
    assert outcome.is_complete is False
    assert outcome.pending_field == "withdrawal_transaction_id"
    assert [s.key for s in outcome.invalid_fields] == ["withdrawal_transaction_id"]


async def test_previously_valid_value_can_be_corrected():
    eng = engine()
    eng._ai.extractions = [
        ExtractionResult(fields=[ExtractedField(key="user_id", value="U-999")])
    ]
    state = ConversationState(
        latest_message="actually my user id is U-999",
        complaint_type="withdrawal",
        collected=_all_valid_withdrawal(),
    )
    outcome = await eng.advance(state)
    user_id = next(f for f in outcome.fields if f.key == "user_id")
    assert user_id.value == "U-999"
    assert user_id.changed is True
    assert outcome.is_complete is True


async def test_restating_the_same_value_is_not_a_change():
    eng = engine()
    eng._ai.extractions = [
        ExtractionResult(fields=[ExtractedField(key="user_id", value="U-1")])
    ]
    state = ConversationState(
        latest_message="my user id is U-1 as I said",
        complaint_type="withdrawal",
        collected=_all_valid_withdrawal(),
    )
    outcome = await eng.advance(state)
    user_id = next(f for f in outcome.fields if f.key == "user_id")
    assert user_id.value == "U-1"
    assert user_id.changed is False


async def test_deposit_method_is_generic_and_does_not_branch():
    eng = engine()
    eng._ai.classification = Classification(type="deposit", confidence=0.9)
    # A single extraction call - the engine must NOT run a method-specific pass.
    eng._ai.extractions = [
        ExtractionResult(
            fields=[ExtractedField(key="deposit_method", value="my local payment wallet")]
        ),
    ]
    state = ConversationState(
        latest_message="I used my local payment wallet",
        complaint_type="deposit",
        collected=[
            CollectedField("user_id", "U-1", FieldStatus.VALIDATED),
            CollectedField("account_email", "a@example.com", FieldStatus.VALIDATED),
            CollectedField("source_wallet_or_account", "corner shop", FieldStatus.VALIDATED),
            CollectedField("transaction_date", "2026-09-01", FieldStatus.VALIDATED),
            CollectedField("problem_description", "Deposit never credited.", FieldStatus.VALIDATED),
        ],
    )
    outcome = await eng.advance(state)

    # the free-text method is stored verbatim
    values = {f.key: f.value for f in outcome.fields}
    assert values["deposit_method"] == "my local payment wallet"
    assert outcome.method_key == "my local payment wallet"
    # exactly one extraction call was consumed (no second/method pass)
    assert eng._ai.extractions == []
    # no additional fields were required - the complaint is complete
    assert outcome.missing_fields == []
    assert outcome.is_complete is True
    assert {f.key for f in outcome.fields} == {
        "user_id",
        "account_email",
        "source_wallet_or_account",
        "transaction_date",
        "deposit_method",
        "problem_description",
    }
