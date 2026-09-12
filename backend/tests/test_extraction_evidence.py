"""Evidence-backed extraction: every stored value points at the customer's
own words, and a value nothing supports is refused.

The engine tests drive it with a scripted provider, because the point is the
guarantee - "an invented value never becomes a field" - which must hold for
any extractor, including a language model that hallucinates.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.ai.base import ExtractedField, ExtractionResult
from app.ai.providers.rule_based import RuleBasedAIProvider
from app.conversation.engine import ConversationEngine
from app.conversation.state import ConversationState
from app.core.config import settings
from app.db.models.complaint_field import ComplaintField
from app.db.models.ml_prediction import MLPrediction
from app.domain.complaint_schemas.registry import get_registry
from app.domain.complaint_schemas.spec import FieldSpec, FieldType, FieldValidation
from app.domain.evidence import locate_evidence

STRING = FieldSpec(key="user_id", label="User ID", type=FieldType.STRING)
EMAIL = FieldSpec(key="account_email", label="Account email", type=FieldType.EMAIL)
TEXT = FieldSpec(key="problem_description", label="Problem", type=FieldType.TEXT)
DATE = FieldSpec(
    key="transaction_date",
    label="Transaction date",
    type=FieldType.DATE,
    validation=FieldValidation(date_formats=["%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"]),
)


def assert_span(message: str, evidence) -> None:
    assert evidence is not None
    assert message[evidence.start : evidence.end] == evidence.text


# ------------------------------------------------------------------ the locator


def test_exact_match_ignores_case_and_whitespace():
    message = "My User ID is  u-482913 and nothing arrived"
    evidence = locate_evidence(STRING, "U-482913", message)
    assert evidence.method == "exact" and evidence.text == "u-482913"
    assert_span(message, evidence)


def test_identifier_separators_do_not_matter():
    message = "account U 482913, please check"
    evidence = locate_evidence(STRING, "U482913", message)
    assert evidence.method == "normalized"
    assert_span(message, evidence)


def test_an_invented_identifier_is_not_supported():
    assert locate_evidence(STRING, "U-999999", "my id is U-482913") is None


def test_email_evidence_is_case_insensitive():
    message = "registered as Jane.Doe@Example.com"
    assert_span(message, locate_evidence(EMAIL, "jane.doe@example.com", message))


@pytest.mark.parametrize(
    ("message", "value"),
    [
        ("I paid on 08/09/2026 by card", "2026-09-08"),
        ("оплата была 8 сентября 2026", "2026-09-08"),
        ("قمت بالإيداع يوم 8 سبتمبر 2026", "2026-09-08"),
        ("Sep 8, 2026 was the day", "2026-09-08"),
    ],
)
def test_a_normalised_date_is_supported_by_how_the_customer_wrote_it(message, value):
    evidence = locate_evidence(DATE, "2026-09-08", message, normalized_value=value)
    assert evidence is not None and evidence.method in {"date", "exact"}
    assert_span(message, evidence)


@pytest.mark.parametrize("word", ["yesterday", "вчера", "أمس"])
def test_relative_dates_resolve_against_the_received_day(word):
    received = date(2026, 9, 11)
    message = f"I sent it {word} and it is missing"
    ok = locate_evidence(DATE, "2026-09-10", message, reference_date=received)
    assert ok is not None and ok.method == "date"
    assert locate_evidence(DATE, "2026-09-09", message, reference_date=received) is None


def test_a_date_the_message_never_mentions_is_not_supported():
    assert locate_evidence(DATE, "2026-01-01", "it happened on 08/09/2026") is None


def test_free_text_may_paraphrase_but_not_invent():
    message = "I deposited money by card. The balance never changed and support ignores me."
    paraphrase = "Card deposit money balance never changed"
    evidence = locate_evidence(TEXT, paraphrase, message)
    assert evidence is not None and evidence.method == "overlap"
    assert_span(message, evidence)
    assert locate_evidence(TEXT, "Customer lost their passport abroad", message) is None


def test_evidence_is_bounded_in_length():
    message = "word " * 200
    evidence = locate_evidence(TEXT, message.strip(), message)
    assert evidence is not None and len(evidence.text) <= 300


# ------------------------------------------------------------------ the engine


class ScriptedExtraction(RuleBasedAIProvider):
    """The rule-based provider, except extraction returns a fixed answer."""

    def __init__(self, fields: list[ExtractedField]):
        self._fields = fields

    async def extract(self, message, specs, known=None, pending_field=None):
        return ExtractionResult(fields=self._fields)


def withdrawal_state(message: str, **extra) -> ConversationState:
    return ConversationState(latest_message=message, complaint_type="withdrawal", **extra)


async def test_an_invented_value_never_becomes_a_field():
    provider = ScriptedExtraction([ExtractedField(key="user_id", value="U-000001")])
    engine = ConversationEngine(provider, get_registry())
    outcome = await engine.advance(withdrawal_state("My withdrawal is late, please help"))

    assert "user_id" not in {f.key for f in outcome.fields}
    assert outcome.extraction.rejected == ["user_id"]
    assert outcome.pending_field == "user_id", "the field is asked for instead"


async def test_a_supported_value_carries_its_evidence():
    provider = ScriptedExtraction([ExtractedField(key="user_id", value="U-482913")])
    engine = ConversationEngine(provider, get_registry())
    message = "withdrawal late, my id is U-482913"
    outcome = await engine.advance(withdrawal_state(message))

    field = next(f for f in outcome.fields if f.key == "user_id")
    assert field.evidence is not None and field.evidence.text == "U-482913"
    assert outcome.extraction.accepted == {"user_id": "exact"}


async def test_an_invented_correction_cannot_overwrite_a_valid_value():
    from app.conversation.state import CollectedField
    from app.domain.enums import FieldStatus

    provider = ScriptedExtraction([ExtractedField(key="user_id", value="U-666")])
    engine = ConversationEngine(provider, get_registry())
    state = withdrawal_state(
        "any update?",
        collected=[CollectedField("user_id", "U-482913", FieldStatus.VALIDATED)],
    )
    outcome = await engine.advance(state)
    assert next(f for f in outcome.fields if f.key == "user_id").value == "U-482913"


async def test_enforcement_can_be_switched_off_for_diagnosis(monkeypatch):
    monkeypatch.setattr(settings, "extraction_require_evidence", False)
    provider = ScriptedExtraction([ExtractedField(key="user_id", value="U-000001")])
    engine = ConversationEngine(provider, get_registry())
    outcome = await engine.advance(withdrawal_state("My withdrawal is late"))
    field = next(f for f in outcome.fields if f.key == "user_id")
    assert field.value == "U-000001" and field.evidence is None


async def test_relative_dates_use_the_messages_received_day():
    provider = ScriptedExtraction([ExtractedField(key="transaction_date", value="2026-09-10")])
    engine = ConversationEngine(provider, get_registry())
    state = ConversationState(
        latest_message="I deposited yesterday by card",
        complaint_type="deposit",
        reference_date=date(2026, 9, 11),
    )
    outcome = await engine.advance(state)
    field = next(f for f in outcome.fields if f.key == "transaction_date")
    assert field.evidence.text == "yesterday"


# The rule-based extractor copies text verbatim, so the check must never
# reject what it finds. A sweep over realistic messages proves the default
# production path lost nothing when enforcement was switched on.
REALISTIC = [
    ("withdrawal", "My withdrawal never arrived. user id U-482913, account email "
                   "jane.doe@example.com, transaction TXN-9f3a12bc."),
    ("withdrawal", "User ID: 583921"),
    ("withdrawal", "transaction id: TXN-77aa01"),
    ("deposit", "I deposited via bank transfer on 08/09/2026, user id U-1, "
                "account email a@b.co, source wallet: Revolut card"),
    ("deposit", "Deposit method: PayPal"),
    ("deposit", "I paid on 8 September using my Visa card"),
    ("withdrawal", "Не пришёл вывод. user id U-555, email ivan@example.ru"),
    ("deposit", "لم يصل الإيداع. user id: 77812 و account email: a@example.com"),
    ("deposit", "дата 8 сентября, пополнение картой"),
]


@pytest.mark.parametrize(("complaint_type", "message"), REALISTIC)
async def test_rule_based_extraction_is_never_rejected(complaint_type, message):
    engine = ConversationEngine(RuleBasedAIProvider(), get_registry())
    for pending in (None, "user_id", "deposit_method", "transaction_date"):
        outcome = await engine.advance(
            ConversationState(
                latest_message=message, complaint_type=complaint_type, pending_field=pending
            )
        )
        assert outcome.extraction.rejected == [], (message, pending)
        for field in outcome.fields:
            if field.changed and field.key != "problem_description":
                assert field.evidence is not None, field.key


# ------------------------------------------------------------------ end to end


COMPLETE_WITHDRAWAL = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)


def test_evidence_is_stored_and_served(client, staff_client, db_session):
    response = client.post(
        "/api/v1/inbox", json={"from_addr": "ev@example.com", "body": COMPLETE_WITHDRAWAL}
    )
    assert response.status_code == 201
    reference = response.json()["ticket_reference"]

    rows = {r.key: r for r in db_session.scalars(select(ComplaintField))}
    assert rows["user_id"].evidence_text == "U-482913"
    assert rows["user_id"].evidence_method == "exact"
    assert COMPLETE_WITHDRAWAL[rows["user_id"].evidence_start : rows["user_id"].evidence_end] == (
        "U-482913"
    )

    detail = staff_client.get(f"/api/v1/tickets/{reference}").json()
    served = {f["key"]: f for f in detail["conversation"]["complaint"]["fields"]}
    assert served["withdrawal_transaction_id"]["evidence_text"] == "TXN-9f3a12bc"


def test_the_extraction_audit_is_logged_without_values(client, db_session):
    client.post("/api/v1/inbox", json={"from_addr": "ev2@example.com", "body": COMPLETE_WITHDRAWAL})
    row = db_session.scalar(select(MLPrediction).where(MLPrediction.task == "extraction"))
    assert row.model_version == "evidence-v1"
    assert row.details["rejected"] == []
    assert set(row.details["accepted"]) >= {"user_id", "account_email"}
    assert "U-482913" not in str(row.details)


def test_a_changed_value_replaces_its_evidence(client, db_session):
    first = client.post(
        "/api/v1/inbox",
        json={"from_addr": "ev3@example.com", "body": "My withdrawal is late. user id U-1"},
    ).json()
    client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "ev3@example.com",
            "body": "account email x@example.com",
            "conversation_id": first["conversation"]["id"],
        },
    )
    rows = {r.key: r for r in db_session.scalars(select(ComplaintField))}
    assert rows["account_email"].evidence_text == "x@example.com"
    assert rows["user_id"].evidence_text == "U-1", "untouched fields keep their evidence"
