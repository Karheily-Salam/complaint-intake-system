from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.conversation import Conversation
    from app.db.models.ticket import Ticket


class Customer(Base, TimestampMixin):
    """A person who has written in.

    Demo and real customers are separate rows even when they share an email
    address. The public demo endpoint accepts an unverified sender address, so
    a single row keyed on email alone would let anyone touch the record of a
    real customer with the same address - writing an attacker-chosen display
    name into what support sees. Scoping identity by ``(email, is_demo)``
    removes that path entirely rather than guarding each individual write.
    """

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("email", "is_demo", name="uq_customer_email_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Indexed but not unique on its own - see the scoped constraint above.
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Mirrors Conversation.is_demo: true for records created through the
    # public demo endpoint, false for real inbound email.
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    tickets: Mapped[list[Ticket]] = relationship(back_populates="customer")
