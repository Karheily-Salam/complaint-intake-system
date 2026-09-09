from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base_class import Base, TimestampMixin
from app.domain.enums import TicketPriority, TicketStatus

if TYPE_CHECKING:
    from app.db.models.complaint import Complaint
    from app.db.models.conversation import Conversation
    from app.db.models.customer import Customer


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)

    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), unique=True, nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)

    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=TicketStatus.NEW, nullable=False, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(20), default=TicketPriority.NORMAL, nullable=False
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    concise_description: Mapped[str] = mapped_column(Text, nullable=False)
    # Denormalised snapshot of the collected fields for fast dashboard rendering.
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    complaint: Mapped[Complaint] = relationship(back_populates="ticket")
    conversation: Mapped[Conversation] = relationship(back_populates="ticket")
    customer: Mapped[Customer] = relationship(back_populates="tickets")
