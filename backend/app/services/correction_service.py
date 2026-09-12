"""Staff corrections: fix the ticket, and keep the correction as training data.

A correction is a staff decision, applied like one: it goes through the same
schema validation as a customer's value, lands on the ticket with
``source=employee``, and never touches the ticket's status. What makes it a
learning signal is the record written alongside it (``ml_feedback``): the
value the system had, where that came from (keyword rules, the classifier,
the extractor), the model version and confidence, the evidence it cited, and
the inbound message it was derived from.

Nothing is retrained here. Recording is the whole job; turning feedback into
a new model is a separate, deliberate step (scripts/export_training_feedback.py).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.complaint_field import ComplaintField
from app.db.models.ml_feedback import MLFeedback
from app.db.models.ml_prediction import MLPrediction
from app.db.models.ticket import Ticket
from app.domain.complaint_schemas.registry import get_registry
from app.domain.enums import FieldSource, FieldStatus
from app.domain.validation import validate_field
from app.services.ticket_service import TicketService

_DEPOSIT_METHOD_KEY = "deposit_method"


class CorrectionError(ValueError):
    """The correction is not valid (unknown type or field, invalid value, no change)."""


class CorrectionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tickets = TicketService(db)
        self.registry = get_registry()

    # ------------------------------------------------------------------ writes

    def correct_classification(
        self, reference: str, corrected_type: str, note: str | None = None
    ) -> tuple[Ticket, MLFeedback] | None:
        ticket = self.tickets.get_ticket(reference)
        if ticket is None:
            return None
        schema = self.registry.try_get(corrected_type)
        if schema is None:
            raise CorrectionError(f"Unknown complaint type '{corrected_type}'")
        complaint = ticket.complaint
        if corrected_type == ticket.type:
            raise CorrectionError("The ticket already has this complaint type")

        prediction = self._latest_prediction(ticket.conversation_id, "classification")
        feedback = self._feedback(
            ticket,
            kind="classification",
            field_key=None,
            original_value=ticket.type,
            corrected_value=corrected_type,
            note=note,
            prediction=prediction,
            original_source=prediction.decided_by if prediction else "unknown",
            original_confidence=(
                (prediction.details or {}).get("decision_confidence") if prediction else None
            ),
            source_message_id=prediction.message_id if prediction else None,
        )

        complaint.type = corrected_type
        ticket.type = corrected_type
        ticket.title = f"{schema.label} - {ticket.customer.email}"
        self.tickets.refresh_snapshot(ticket, complaint)
        self.db.commit()
        return ticket, feedback

    def correct_field(
        self, reference: str, field_key: str, corrected_value: str, note: str | None = None
    ) -> tuple[Ticket, MLFeedback] | None:
        ticket = self.tickets.get_ticket(reference)
        if ticket is None:
            return None
        complaint = ticket.complaint
        schema = self.registry.try_get(complaint.type)
        specs = schema.fields_for() if schema else []
        spec = next((s for s in specs if s.key == field_key), None)
        if spec is None:
            raise CorrectionError(f"'{field_key}' is not a field of this complaint type")
        result = validate_field(spec, corrected_value)
        if not result.ok:
            raise CorrectionError(result.error or "Invalid value")
        value = result.normalized_value or corrected_value.strip()

        row = next((f for f in complaint.fields if f.key == field_key), None)
        if row is not None and (row.value or "").strip().lower() == value.lower():
            raise CorrectionError("The field already has this value")

        prediction = (
            self._prediction_for_message(row.source_message_id, "extraction")
            if row is not None and row.source_message_id
            else None
        )
        feedback = self._feedback(
            ticket,
            kind="field",
            field_key=field_key,
            original_value=row.value if row else None,
            corrected_value=value,
            note=note,
            prediction=prediction,
            original_source=row.source if row else "missing",
            original_confidence=row.confidence if row else None,
            original_evidence=row.evidence_text if row else None,
            source_message_id=row.source_message_id if row else None,
        )

        if row is None:
            row = ComplaintField(complaint_id=complaint.id, key=field_key)
            complaint.fields.append(row)
        row.value = value
        row.status = FieldStatus.VALIDATED
        row.source = FieldSource.EMPLOYEE
        row.validation_error = None
        row.confidence = None
        row.source_message_id = None
        row.evidence_text = row.evidence_start = row.evidence_end = row.evidence_method = None
        if field_key == _DEPOSIT_METHOD_KEY:
            complaint.method_key = value[:50]
        self.tickets.refresh_snapshot(ticket, complaint)
        self.db.commit()
        return ticket, feedback

    # ------------------------------------------------------------------ reads

    def for_ticket(self, reference: str) -> list[MLFeedback] | None:
        ticket = self.tickets.get_ticket(reference)
        if ticket is None:
            return None
        return list(
            self.db.scalars(
                select(MLFeedback)
                .where(MLFeedback.ticket_id == ticket.id)
                .order_by(MLFeedback.id.desc())
            )
        )

    # ------------------------------------------------------------------ helpers

    def _feedback(self, ticket: Ticket, *, prediction: MLPrediction | None, **values) -> MLFeedback:
        conversation = ticket.conversation
        feedback = MLFeedback(
            ticket_id=ticket.id,
            complaint_id=ticket.complaint_id,
            conversation_id=ticket.conversation_id,
            is_demo=conversation.is_demo,
            language_code=conversation.language_code,
            model_name=prediction.model_name if prediction else None,
            model_version=prediction.model_version if prediction else None,
            prediction_id=prediction.id if prediction else None,
            **values,
        )
        self.db.add(feedback)
        self.db.flush()
        return feedback

    def _latest_prediction(self, conversation_id: int, task: str) -> MLPrediction | None:
        return self.db.scalar(
            select(MLPrediction)
            .where(MLPrediction.conversation_id == conversation_id, MLPrediction.task == task)
            .order_by(MLPrediction.id.desc())
            .limit(1)
        )

    def _prediction_for_message(self, message_id: int, task: str) -> MLPrediction | None:
        return self.db.scalar(
            select(MLPrediction)
            .where(MLPrediction.message_id == message_id, MLPrediction.task == task)
            .order_by(MLPrediction.id.desc())
            .limit(1)
        )
