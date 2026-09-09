from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin
from app.domain.enums import ConversationStatus

if TYPE_CHECKING:
    from app.db.models.complaint import Complaint
    from app.db.models.customer import Customer
    from app.db.models.message import Message
    from app.db.models.ticket import Ticket


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True, nullable=False)

    channel: Mapped[str] = mapped_column(String(30), default="email", nullable=False)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default=ConversationStatus.OPEN, nullable=False, index=True
    )
    # ISO 639-1 code of the customer's language, detected from their messages
    # (see ConversationEngine._resolve_language). Null until the first inbound
    # message is processed.
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    complaint: Mapped[Complaint | None] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )
    ticket: Mapped[Ticket | None] = relationship(back_populates="conversation", uselist=False)
