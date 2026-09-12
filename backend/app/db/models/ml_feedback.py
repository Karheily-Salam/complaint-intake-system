from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin


class MLFeedback(Base, TimestampMixin):
    """A staff correction, kept as a labelled training example.

    When an agent fixes a complaint type or a collected field, the correction
    is applied to the ticket (staff are the authority) *and* recorded here
    with everything needed to learn from it later: what the system originally
    had, which layer and model version produced it, how confident it was, the
    evidence it cited, and which inbound message was the input.

    Recording is all that happens. Nothing retrains automatically: exporting
    feedback (scripts/export_training_feedback.py), retraining and shipping a
    new model are deliberate, reviewed steps.
    """

    __tablename__ = "ml_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    # classification | field
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False, index=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"), nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # None for a classification correction.
    field_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)

    # Provenance of the original value: which layer decided it (rules | ml |
    # provider | customer_message | missing), which model version, how sure.
    original_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    original_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    prediction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ml_predictions.id"), nullable=True
    )
    original_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The inbound message the original value was derived from - the model
    # input a future training example is built from.
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )

    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Set by the export script once an example has been written to a dataset,
    # so the same correction is not exported twice by accident.
    exported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
