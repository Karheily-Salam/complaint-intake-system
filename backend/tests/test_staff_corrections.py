"""Staff corrections: applied to the ticket as a staff decision, and recorded -
with the original prediction and its provenance - as training feedback.

Nothing is retrained. The tests pin what gets recorded, that invalid
corrections are refused without side effects, that the ticket's status is
never touched, and that the export masks identifiers and honours exclusions.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import func, select

from app.db.models.complaint_field import ComplaintField
from app.db.models.ml_feedback import MLFeedback
from app.email.base import InboundEmail
from app.ml.classifier import get_classifier
from app.services.intake_service import IntakeService

COMPLETE_WITHDRAWAL = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)


def open_ticket(client, body=COMPLETE_WITHDRAWAL, sender="fix@example.com"):
    response = client.post("/api/v1/inbox", json={"from_addr": sender, "body": body})
    assert response.status_code == 201
    data = response.json()
    assert data["ticket_reference"], data
    return data


def correct(staff_client, reference, **payload):
    return staff_client.post(f"/api/v1/tickets/{reference}/corrections", json=payload)


def feedback_count(db):
    return db.scalar(select(func.count()).select_from(MLFeedback))


# ------------------------------------------------------------------ fields


def test_a_field_correction_is_applied_and_recorded(client, staff_client, db_session):
    reference = open_ticket(client)["ticket_reference"]
    response = correct(
        staff_client, reference, kind="field", field_key="user_id",
        corrected_value="U-000777", note="customer confirmed by phone",
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["ticket"]["structured_data"]["fields"]["user_id"] == "U-000777"
    assert body["ticket"]["status"] == "new", "a correction never changes status"

    feedback = body["feedback"]
    assert feedback["kind"] == "field" and feedback["field_key"] == "user_id"
    assert feedback["original_value"] == "U-482913"
    assert feedback["corrected_value"] == "U-000777"
    assert feedback["original_source"] == "customer_message"
    assert feedback["model_name"] == "extractor:rule_based"
    assert feedback["model_version"] == "evidence-v1"

    row = db_session.scalar(select(MLFeedback))
    assert row.original_evidence == "U-482913" and row.source_message_id is not None
    field = db_session.scalar(select(ComplaintField).where(ComplaintField.key == "user_id"))
    assert field.source == "employee" and field.evidence_text is None


def test_a_field_from_another_complaint_type_is_refused(client, staff_client, db_session):
    reference = open_ticket(client)["ticket_reference"]
    # deposit_method belongs to deposits; this is a withdrawal ticket.
    assert correct(
        staff_client, reference, kind="field", field_key="deposit_method", corrected_value="card"
    ).status_code == 422
    assert feedback_count(db_session) == 0


def test_an_invalid_value_is_refused_without_side_effects(client, staff_client, db_session):
    reference = open_ticket(client)["ticket_reference"]
    response = correct(
        staff_client, reference, kind="field", field_key="account_email",
        corrected_value="not-an-email",
    )
    assert response.status_code == 422
    assert feedback_count(db_session) == 0


def test_an_unknown_field_or_an_unchanged_value_is_refused(client, staff_client, db_session):
    reference = open_ticket(client)["ticket_reference"]
    assert correct(
        staff_client, reference, kind="field", field_key="favourite_colour", corrected_value="x"
    ).status_code == 422
    assert correct(
        staff_client, reference, kind="field", field_key="user_id", corrected_value="u-482913"
    ).status_code == 422
    assert correct(staff_client, reference, kind="field", corrected_value="x").status_code == 422
    assert feedback_count(db_session) == 0


def test_the_customer_cannot_silently_undo_a_staff_correction(client, staff_client):
    data = open_ticket(client)
    reference = data["ticket_reference"]
    correct(staff_client, reference, kind="field", field_key="user_id", corrected_value="U-000777")
    client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "fix@example.com",
            "body": "thanks, my user id U-482913",
            "conversation_id": data["conversation"]["id"],
        },
    )
    detail = staff_client.get(f"/api/v1/tickets/{reference}").json()
    assert detail["structured_data"]["fields"]["user_id"] == "U-000777"


# ------------------------------------------------------------------ classification


def test_a_type_correction_records_the_deciding_layer(client, staff_client, db_session):
    reference = open_ticket(client)["ticket_reference"]
    response = correct(
        staff_client, reference, kind="classification", corrected_value="deposit"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ticket"]["type"] == "deposit"
    assert body["ticket"]["title"].startswith("Deposit problem")
    assert body["ticket"]["status"] == "new"

    feedback = body["feedback"]
    assert feedback["original_value"] == "withdrawal"
    assert feedback["original_source"] == "rules", "a keyword decided this one"
    assert feedback["model_version"] == get_classifier().version
    assert db_session.scalar(select(MLFeedback)).prediction_id is not None


def test_correcting_a_model_decision_is_attributed_to_the_model(client, staff_client):
    data = open_ticket(
        client,
        body="Не могу войти в аккаунт, пишет неверный пароль даже после сброса.",
        sender="ru@example.com",
    )
    response = correct(
        staff_client, data["ticket_reference"], kind="classification", corrected_value="withdrawal"
    )
    assert response.status_code == 201
    assert response.json()["feedback"]["original_source"] == "ml"
    assert response.json()["feedback"]["language_code"] == "ru"


def test_an_unknown_or_unchanged_type_is_refused(client, staff_client, db_session):
    reference = open_ticket(client)["ticket_reference"]
    assert correct(
        staff_client, reference, kind="classification", corrected_value="refund"
    ).status_code == 422
    assert correct(
        staff_client, reference, kind="classification", corrected_value="withdrawal"
    ).status_code == 422
    assert feedback_count(db_session) == 0


def test_unknown_ticket_is_404(staff_client):
    response = correct(staff_client, "999999", kind="classification", corrected_value="deposit")
    assert response.status_code == 404


# ------------------------------------------------------------------ reading feedback


def test_corrections_are_listed_per_ticket_newest_first(client, staff_client):
    reference = open_ticket(client)["ticket_reference"]
    correct(staff_client, reference, kind="field", field_key="user_id", corrected_value="U-1")
    correct(staff_client, reference, kind="classification", corrected_value="deposit")
    items = staff_client.get(f"/api/v1/tickets/{reference}/corrections").json()
    assert [i["kind"] for i in items] == ["classification", "field"]


def test_the_summary_excludes_demo_feedback_unless_asked(client, staff_client):
    reference = open_ticket(client)["ticket_reference"]
    correct(staff_client, reference, kind="classification", corrected_value="deposit")

    real_only = staff_client.get("/api/v1/ml/feedback").json()
    assert real_only["summary"]["total"] == 0 and real_only["items"] == []

    everything = staff_client.get("/api/v1/ml/feedback?include_demo=true").json()
    assert everything["summary"]["total"] == 1
    rules = everything["summary"]["classification_by_layer"]["rules"]
    assert rules["corrected"] == 1 and rules["decisions"] >= 1


async def test_real_feedback_counts_by_default(staff_client, db_session):
    result = await IntakeService(db_session).handle_inbound_email(
        InboundEmail(
            message_id=f"<{uuid.uuid4().hex}@example.com>",
            from_addr="real@example.com",
            to_addr="complaints@example.com",
            body=COMPLETE_WITHDRAWAL,
        )
    )
    correct(
        staff_client, result.ticket_reference, kind="classification", corrected_value="deposit"
    )
    summary = staff_client.get("/api/v1/ml/feedback").json()["summary"]
    assert summary["total"] == 1 and summary["by_kind"] == {"classification": 1}
    assert summary["classification_by_layer"]["rules"]["correction_rate"] == 1.0


# ------------------------------------------------------------------ export


def test_export_masks_identifiers_and_honours_exclusions(client, staff_client, db_session):
    from scripts.export_training_feedback import build_records

    kept = open_ticket(client, sender="keep@example.com")["ticket_reference"]
    dropped = open_ticket(client, sender="drop@example.com")["ticket_reference"]
    correct(staff_client, kept, kind="field", field_key="user_id", corrected_value="U-000777")
    correct(staff_client, dropped, kind="field", field_key="user_id", corrected_value="U-000888")

    assert not build_records(
        db_session, include_demo=False, excluded=set(), include_exported=False
    )

    rows = build_records(
        db_session, include_demo=True, excluded={dropped}, include_exported=False
    )
    assert len(rows) == 1
    record = rows[0][1]
    blob = json.dumps(record, ensure_ascii=False)
    for secret in ("U-482913", "U-000777", "jane.doe@example.com", "TXN-9f3a12bc"):
        assert secret not in blob
    assert record["field_key"] == "user_id" and "withdrawal" in record["text"]
