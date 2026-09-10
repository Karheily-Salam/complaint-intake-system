"""Staff ticket API.

Every route here can return real customer data (email addresses, collected
identifiers, full message bodies) or mutate a real ticket, so the whole router
sits behind the staff API key. The unauthenticated demo equivalents live in
``routes/demo.py`` and are restricted to synthetic conversations.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import DbDep
from app.api.security import StaffAuth
from app.schemas.ticket import TicketDetail, TicketStatusUpdate, TicketSummary
from app.services.ticket_service import TicketService

# The dependency is declared on the router, not per route, so a route added
# later is protected by default rather than by remembering to annotate it.
router = APIRouter(dependencies=[StaffAuth])


@router.get("", response_model=list[TicketSummary])
def list_tickets(
    db: DbDep,
    # Bounded: an unbounded limit lets one request pull the whole table into
    # memory and serialise it, on a host with 2GB of RAM.
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TicketSummary]:
    return TicketService(db).list_tickets(limit=limit)  # type: ignore[return-value]


@router.get("/{reference}", response_model=TicketDetail)
def get_ticket(reference: str, db: DbDep) -> TicketDetail:
    ticket = TicketService(db).get_ticket(reference)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket  # type: ignore[return-value]


@router.patch("/{reference}", response_model=TicketDetail)
def update_ticket_status(reference: str, payload: TicketStatusUpdate, db: DbDep) -> TicketDetail:
    try:
        ticket = TicketService(db).update_status(reference, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket  # type: ignore[return-value]
