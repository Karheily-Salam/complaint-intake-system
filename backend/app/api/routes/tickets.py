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
from app.ml.similarity import find_similar
from app.repositories.ticket_repo import TicketQuery
from app.schemas.ml import (
    CorrectionIn,
    CorrectionOut,
    FeedbackOut,
    SimilarTicketOut,
    SimilarTicketsOut,
)
from app.schemas.ticket import (
    TicketDetail,
    TicketReplyIn,
    TicketReplyOut,
    TicketStatusUpdate,
    TicketSummary,
)
from app.services.correction_service import CorrectionError, CorrectionService
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


@router.get("/{reference}/similar", response_model=SimilarTicketsOut)
def similar_tickets(
    reference: str,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> SimilarTicketsOut:
    """Tickets that look like this one, and which may be duplicates.

    Suggestions only: nothing is merged or changed. Computing them may store
    this ticket's embedding if the background worker has not yet, which is
    derived data, never ticket state.
    """
    ticket = TicketService(db).get_ticket(reference)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    result = find_similar(db, ticket, limit=limit)
    db.commit()
    return SimilarTicketsOut(
        reference=ticket.reference,
        model_version=result.model_version,
        similar_threshold=result.similar_threshold,
        duplicate_threshold=result.duplicate_threshold,
        items=[SimilarTicketOut(**vars(item)) for item in result.items],
    )


@router.post("/{reference}/corrections", response_model=CorrectionOut, status_code=201)
def correct_ticket(reference: str, payload: CorrectionIn, db: DbDep) -> CorrectionOut:
    """Correct the complaint type or one collected field, as a staff decision.

    The correction is validated against the complaint schema and applied to
    the ticket (never its status), and the original prediction is kept next
    to the corrected value as training feedback. No model is retrained.
    """
    service = CorrectionService(db)
    try:
        if payload.kind == "classification":
            result = service.correct_classification(
                reference, payload.corrected_value.strip(), payload.note
            )
        else:
            if not payload.field_key:
                raise CorrectionError("field_key is required for a field correction")
            result = service.correct_field(
                reference, payload.field_key, payload.corrected_value, payload.note
            )
    except CorrectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket, feedback = result
    fresh = TicketService(db).get_ticket(ticket.reference)
    return CorrectionOut(
        feedback=FeedbackOut.model_validate(feedback),
        ticket=TicketDetail.model_validate(fresh),
    )


@router.get("/{reference}/corrections", response_model=list[FeedbackOut])
def ticket_corrections(reference: str, db: DbDep) -> list[FeedbackOut]:
    items = CorrectionService(db).for_ticket(reference)
    if items is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return [FeedbackOut.model_validate(item) for item in items]


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
