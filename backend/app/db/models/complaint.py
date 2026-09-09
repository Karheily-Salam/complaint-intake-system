from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin
from app.domain.enums import ComplaintStatus

if TYPE_CHECKING:
    from app.db.models.complaint_field import ComplaintField
    from app.db.models.conversation import Conversation
    from app.db.models.ticket import Ticket


class Complaint(Base, TimestampMixin):
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), unique=True, nullable=False
    )

    # ComplaintType value; nullable until the engine classifies the conversation.
    type: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    # Free-text deposit method as stated by the customer (denormalised copy of the
    # ``deposit_method`` collected field). The engine never branches on it.
    method_key: Mapped[str | None] = mapped_column(String(50), nullable=True)

    concise_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=ComplaintStatus.DRAFT, nullable=False, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="complaint")
    fields: Mapped[list[ComplaintField]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan"
    )
    ticket: Mapped[Ticket | None] = relationship(back_populates="complaint", uselist=False)
