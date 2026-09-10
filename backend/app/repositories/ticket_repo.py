from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.complaint import Complaint
from app.db.models.conversation import Conversation
from app.db.models.ticket import Ticket


class TicketRepository:
    """Data access for tickets.

    ``demo_only`` is threaded through every read rather than being applied by
    the caller afterwards: the public endpoints must be unable to *load* a
    real customer's ticket at all, not merely decline to render it.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_reference(self, reference: str, *, demo_only: bool = False) -> Ticket | None:
        stmt = (
            select(Ticket)
            .where(Ticket.reference == reference)
            .options(
                selectinload(Ticket.customer),
                selectinload(Ticket.conversation).selectinload(Conversation.messages),
                selectinload(Ticket.conversation)
                .selectinload(Conversation.complaint)
                .selectinload(Complaint.fields),
            )
        )
        if demo_only:
            stmt = stmt.join(Conversation, Ticket.conversation_id == Conversation.id).where(
                Conversation.is_demo.is_(True)
            )
        return self.db.scalar(stmt)

    def list(self, limit: int = 100, *, demo_only: bool = False) -> list[Ticket]:
        stmt = (
            select(Ticket)
            .order_by(Ticket.created_at.desc())
            .limit(limit)
            .options(selectinload(Ticket.customer))
        )
        if demo_only:
            stmt = stmt.join(Conversation, Ticket.conversation_id == Conversation.id).where(
                Conversation.is_demo.is_(True)
            )
        return list(self.db.scalars(stmt))

    def add(self, ticket: Ticket) -> Ticket:
        self.db.add(ticket)
        self.db.flush()
        return ticket
