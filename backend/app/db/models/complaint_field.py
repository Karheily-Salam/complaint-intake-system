from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin
from app.domain.enums import FieldSource, FieldStatus

if TYPE_CHECKING:
    from app.db.models.complaint import Complaint


class ComplaintField(Base, TimestampMixin):
    """A single piece of collected information for a complaint.

    The set of *possible* keys is defined by the complaint schema registry, not
    by this table - storage stays schema-agnostic so new complaint types and
    deposit methods can be added through configuration only.
    """

    __tablename__ = "complaint_fields"
    __table_args__ = (UniqueConstraint("complaint_id", "key", name="uq_complaint_field_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), index=True, nullable=False
    )

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=FieldStatus.EXTRACTED, nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(20), default=FieldSource.CUSTOMER_MESSAGE, nullable=False
    )
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The exact text in the customer's message that supports ``value``, and
    # how it was matched (see app.domain.evidence). Offsets are into that
    # message's body with quoted reply history removed. Null when the value
    # did not come from message text - the engine's own summary, or a staff
    # correction.
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_method: Mapped[str | None] = mapped_column(String(20), nullable=True)

    complaint: Mapped[Complaint] = relationship(back_populates="fields")
