"""Simulated inbound email endpoint.

In production a poller would call ``EmailProvider.fetch_new()``; for the prototype
the frontend Mailbox Simulator posts here to represent a customer sending mail.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import DbDep
from app.schemas.conversation import InboundEmailIn, IntakeResult
from app.services.intake_service import IntakeService

router = APIRouter()


@router.post("", response_model=IntakeResult, status_code=201)
async def receive_email(payload: InboundEmailIn, db: DbDep) -> IntakeResult:
    try:
        return await IntakeService(db).handle_inbound(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
