from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base_class import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.conversation import Conversation


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True, nullable=False
    )

    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # inbound | outbound
    # The RFC 5322 Message-ID of the real email this row represents: the
    # customer's own for inbound, ours for outbound. Null for messages that
    # never touched a real mailbox (the local /inbox dev simulator).
    #
    # Serves two purposes, which is why it is a real indexed column rather
    # than a raw_meta key:
    #   - idempotency: a redelivered inbound email is recognised and skipped
    #     (unique, so a duplicate can never be inserted even under a race)
    #   - threading: a reply's In-Reply-To/References is matched against the
    #     outbound Message-ID we sent, resolving the conversation without
    #     relying on the customer's address (one customer may have several
    #     open complaints).
    external_message_id: Mapped[str | None] = mapped_column(
        String(500), unique=True, index=True, nullable=True
    )
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    raw_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
