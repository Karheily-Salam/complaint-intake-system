"""Turns a validated complaint into an employee-facing ticket."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.complaint import Complaint
from app.db.models.conversation import Conversation
from app.db.models.ticket import Ticket
from app.domain.complaint_schemas.registry import get_registry
from app.domain.enums import ComplaintStatus, TicketStatus
from app.repositories.ticket_repo import TicketRepository


class TicketService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tickets = TicketRepository(db)
        self.registry = get_registry()

    def create_for_complaint(self, conversation: Conversation, complaint: Complaint) -> Ticket:
        if complaint.ticket is not None:
            return complaint.ticket

        schema = self.registry.try_get(complaint.type)
        label = schema.label if schema else (complaint.type or "Complaint")

        collected = {f.key: f.value for f in complaint.fields if f.value is not None}
        structured = {
            "complaint_type": complaint.type,
            "method_key": complaint.method_key,
            "fields": collected,
        }

        reference = self._make_reference()
        description = complaint.concise_description or collected.get(
            "problem_description", "No description provided."
        )

        ticket = Ticket(
            reference=reference,
            complaint_id=complaint.id,
            conversation_id=conversation.id,
            customer_id=conversation.customer_id,
            type=complaint.type or "other",
            status=TicketStatus.NEW,
            title=f"{label} - {conversation.customer.email}",
            concise_description=description,
            structured_data=structured,
        )
        self.tickets.add(ticket)

        complaint.status = ComplaintStatus.TICKETED
        return ticket

    def _make_reference(self) -> str:
        seq = self.tickets.next_sequence()
        return f"{settings.ticket_reference_prefix}-{seq:06d}"

    # ---- dashboard reads ----

    def list_tickets(self, limit: int = 100) -> list[Ticket]:
        return self.tickets.list(limit=limit)

    def get_ticket(self, reference: str) -> Ticket | None:
        return self.tickets.get_by_reference(reference)

    def update_status(self, reference: str, status: str) -> Ticket | None:
        ticket = self.tickets.get_by_reference(reference)
        if ticket is None:
            return None
        if status not in {s.value for s in TicketStatus}:
            raise ValueError(f"Invalid ticket status '{status}'")
        ticket.status = status
        self.db.commit()
        return ticket
