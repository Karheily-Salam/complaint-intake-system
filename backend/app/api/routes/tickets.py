"""Staff ticket API.

Every route here can return real customer data (email addresses, collected
identifiers, full message bodies) or mutate a real ticket, so the whole router
sits behind the staff API key. The unauthenticated demo equivalents live in
``routes/demo.py`` and are restricted to synthetic conversations.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.deps import DbDep
from app.api.security import StaffAuth
from app.repositories.ticket_repo import TicketQuery
from app.schemas.ticket import (
    TicketDetail,
    TicketReplyIn,
    TicketReplyOut,
    TicketStatusUpdate,
    TicketSummary,
)
from app.services.ticket_reply_service import ReplyNotPossible, TicketReplyService
from app.services.ticket_service import TicketService

DEFAULT_PAGE_SIZE = 20

# The dependency is declared on the router, not per route, so a route added
# later is protected by default rather than by remembering to annotate it.
router = APIRouter(dependencies=[StaffAuth])


@router.get("", response_model=list[TicketSummary])
def list_tickets(
    db: DbDep,
    response: Response,
    q: Annotated[
        str | None, Query(max_length=320, description="reference or customer email")
    ] = None,
    status: Annotated[str | None, Query(description="ticket status")] = None,
    complaint_type: Annotated[str | None, Query(alias="type")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    # Bounded: an unbounded page size lets one request pull the whole table
    # into memory and serialise it, on a host with 2GB of RAM.
    page_size: Annotated[int, Query(ge=1, le=200)] = DEFAULT_PAGE_SIZE,
    limit: Annotated[int | None, Query(ge=1, le=500, deprecated=True)] = None,
) -> list[TicketSummary]:
    """One page of tickets, newest first.

    Filtering happens in SQL rather than the browser, so the dashboard never
    receives rows an agent did not ask for. The total match count is returned
    in ``X-Total-Count`` rather than by changing the response body, which keeps
    the existing list contract intact for anything already consuming it.

    ``limit`` is the original parameter and still works; it is treated as the
    page size.
    """
    try:
        result = TicketService(db).search_tickets(
            TicketQuery(
                search=q,
                status=status,
                complaint_type=complaint_type,
                page=page,
                page_size=limit or page_size,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response.headers["X-Total-Count"] = str(result.total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return result.items  # type: ignore[return-value]


@router.get("/{reference}", response_model=TicketDetail)
def get_ticket(reference: str, db: DbDep) -> TicketDetail:
    ticket = TicketService(db).get_ticket(reference)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket  # type: ignore[return-value]


@router.post("/{reference}/reply", response_model=TicketReplyOut)
async def reply_to_customer(
    reference: str, payload: TicketReplyIn, db: DbDep
) -> TicketReplyOut:
    """Email the ticket's customer, on the existing conversation thread.

    The recipient comes from the ticket, not from the request, and the result
    states whether the configured provider actually delivered anything - see
    TicketReplyService.
    """
    try:
        result = await TicketReplyService(db).reply(
            reference, body=payload.body, subject=payload.subject
        )
    except ReplyNotPossible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketReplyOut(**vars(result))


@router.patch("/{reference}", response_model=TicketDetail)
def update_ticket_status(reference: str, payload: TicketStatusUpdate, db: DbDep) -> TicketDetail:
    try:
        ticket = TicketService(db).update_status(reference, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket  # type: ignore[return-value]
