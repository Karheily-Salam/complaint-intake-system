"""Public demo API - synthetic data only, no credential required.

These are the read endpoints the portfolio demo needs so a visitor can watch a
complaint turn into a ticket. They are deliberately *not* the staff endpoints
with a filter bolted on by the UI: every query here is restricted to
conversations flagged ``is_demo`` in the database, so a real customer's
conversation cannot be loaded through this router at all, whatever id or
reference is supplied.

Demo data is whatever a visitor typed into the simulator. It never contains a
real complaint, because real inbound mail is never flagged as demo.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import DbDep
from app.schemas.conversation import ConversationOut
from app.schemas.ticket import TicketDetail, TicketSummary
from app.services.conversation_service import ConversationService
from app.services.ticket_service import TicketService

router = APIRouter()


@router.get("/tickets", response_model=list[TicketSummary])
def list_demo_tickets(
    db: DbDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TicketSummary]:
    return TicketService(db).list_tickets(limit=limit, demo_only=True)  # type: ignore[return-value]


@router.get("/tickets/{reference}", response_model=TicketDetail)
def get_demo_ticket(reference: str, db: DbDep) -> TicketDetail:
    ticket = TicketService(db).get_ticket(reference, demo_only=True)
    if ticket is None:
        # A real ticket that exists is reported the same way as one that does
        # not, so this endpoint cannot be used to probe which references are
        # real.
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket  # type: ignore[return-value]


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_demo_conversation(conversation_id: int, db: DbDep) -> ConversationOut:
    convo = ConversationService(db).get(conversation_id, demo_only=True)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo  # type: ignore[return-value]
