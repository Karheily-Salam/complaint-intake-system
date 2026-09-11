from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models.complaint import Complaint
from app.db.models.conversation import Conversation
from app.db.models.customer import Customer
from app.db.models.ticket import Ticket


@dataclass(frozen=True)
class TicketQuery:
    """Filters a support agent can apply to the ticket list.

    Applied in SQL rather than in the browser: a dashboard that fetches every
    ticket and filters client-side stops working as soon as the table is
    bigger than a page, and would ship data the agent never asked to see.
    """

    search: str | None = None
    status: str | None = None
    complaint_type: str | None = None
    page: int = 1
    page_size: int = 20
    demo_only: bool = False


@dataclass(frozen=True)
class TicketPage:
    items: list[Ticket]
    total: int


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

    def _apply_filters(self, stmt: Select, query: TicketQuery) -> Select:
        if query.demo_only:
            stmt = stmt.join(Conversation, Ticket.conversation_id == Conversation.id).where(
                Conversation.is_demo.is_(True)
            )
        if query.status:
            stmt = stmt.where(Ticket.status == query.status)
        if query.complaint_type:
            stmt = stmt.where(Ticket.type == query.complaint_type)
        if query.search:
            # An agent searches by the two things they actually have: the
            # reference the customer quotes, or the address they wrote from.
            term = f"%{query.search.strip()}%"
            stmt = stmt.join(Customer, Ticket.customer_id == Customer.id).where(
                or_(Ticket.reference.ilike(term), Customer.email.ilike(term))
            )
        return stmt

    def search(self, query: TicketQuery) -> TicketPage:
        """One page of tickets plus the total matching count."""
        total = (
            self.db.scalar(self._apply_filters(select(func.count()).select_from(Ticket), query))
            or 0
        )

        stmt = (
            self._apply_filters(select(Ticket), query)
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
            .options(selectinload(Ticket.customer))
        )
        return TicketPage(items=list(self.db.scalars(stmt)), total=total)

    def list(self, limit: int = 100, *, demo_only: bool = False) -> list[Ticket]:
        """Unpaginated listing, kept for the demo endpoints."""
        return self.search(
            TicketQuery(page=1, page_size=limit, demo_only=demo_only)
        ).items

    def add(self, ticket: Ticket) -> Ticket:
        self.db.add(ticket)
        self.db.flush()
        return ticket
