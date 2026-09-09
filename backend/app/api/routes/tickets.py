from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import DbDep
from app.schemas.ticket import TicketDetail, TicketStatusUpdate, TicketSummary
from app.services.ticket_service import TicketService

router = APIRouter()


@router.get("", response_model=list[TicketSummary])
def list_tickets(db: DbDep, limit: int = 100) -> list[TicketSummary]:
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
