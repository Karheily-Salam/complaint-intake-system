from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.complaint import Complaint
from app.db.models.conversation import Conversation
from app.db.models.ticket import Ticket


class TicketRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_reference(self, reference: str) -> Ticket | None:
        return self.db.scalar(
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

    def list(self, limit: int = 100) -> list[Ticket]:
        return list(
            self.db.scalars(
                select(Ticket)
                .order_by(Ticket.created_at.desc())
                .limit(limit)
                .options(selectinload(Ticket.customer))
            )
        )

    def add(self, ticket: Ticket) -> Ticket:
        self.db.add(ticket)
        self.db.flush()
        return ticket
