"""Turns a validated complaint into an employee-facing ticket."""

from __future__ import annotations

from sqlalchemy.orm import Session

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

        ticket = Ticket(
            # Placeholder only - unique via complaint_id (itself unique per
            # ticket, see the Ticket model), so the flush below never
            # collides with another row also awaiting its real reference.
            # Replaced with the real numeric reference once the row exists
            # and its auto-incrementing primary key is known - see
            # _make_reference.
            reference=f"pending-{complaint.id}",
            complaint_id=complaint.id,
            conversation_id=conversation.id,
            customer_id=conversation.customer_id,
            type=complaint.type or "other",
            status=TicketStatus.NEW,
            title=f"{label} - {conversation.customer.email}",
            concise_description=self._description(complaint),
            structured_data=self._structured(complaint),
        )
        self.tickets.add(ticket)  # flush assigns ticket.id
        ticket.reference = self._make_reference(ticket.id)
        self.db.flush()
        complaint.status = ComplaintStatus.TICKETED
        return ticket

    def refresh_snapshot(self, ticket: Ticket, complaint: Complaint) -> Ticket:
        """Keep the ticket's denormalised data in step with later corrections.

        Employee-owned fields (status, priority) are never touched.
        """
        ticket.concise_description = self._description(complaint)
        ticket.structured_data = self._structured(complaint)
        return ticket

    def _structured(self, complaint: Complaint) -> dict:
        return {
            "complaint_type": complaint.type,
            "method_key": complaint.method_key,
            "fields": {f.key: f.value for f in complaint.fields if f.value is not None},
        }

    @staticmethod
    def _description(complaint: Complaint) -> str:
        if complaint.concise_description:
            return complaint.concise_description
        for f in complaint.fields:
            if f.key == "problem_description" and f.value:
                return f.value
        return "No description provided."

    @staticmethod
    def _make_reference(ticket_id: int) -> str:
        """A deterministic, numeric-only customer-facing reference.

        Backed directly by the ticket's own auto-incrementing primary key -
        already guaranteed unique and collision-safe by the database itself
        (identical mechanism on SQLite and PostgreSQL), so there is no
        separate counter to keep in sync or race on. Zero-padded only for a
        tidier, fixed-width look; grows naturally past 6 digits if ever
        needed. The AI layer never sees or generates this - it only ever
        phrases whatever value is passed to it (see ReplyRequest.ticket_reference).
        """
        return f"{ticket_id:06d}"

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
