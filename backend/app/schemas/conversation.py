from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.email.base import MAX_BODY_CHARS, MAX_SUBJECT_CHARS
from app.schemas.common import ORMModel
from app.schemas.message import MessageOut


class InboundEmailIn(BaseModel):
    """Simulated inbound customer email (what MockEmailProvider would receive).

    This endpoint is reachable by anyone who can open the demo page, so the
    bounds below are what stop one request from writing an arbitrarily large
    row into the database. They mirror the limits real inbound mail gets in
    app/email/base.py, so both intake paths behave the same.
    """

    from_addr: EmailStr
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    subject: str | None = Field(default=None, max_length=MAX_SUBJECT_CHARS)
    customer_name: str | None = Field(default=None, max_length=200)
    # Continue an existing conversation; omit to start a new one.
    conversation_id: int | None = None


class CollectedField(ORMModel):
    key: str
    value: str | None
    status: str
    source: str
    confidence: float | None
    validation_error: str | None
    # Where the value came from in the customer's message (see
    # app.domain.evidence). Null for summaries and staff corrections.
    evidence_text: str | None = None
    evidence_start: int | None = None
    evidence_end: int | None = None
    evidence_method: str | None = None


class ComplaintOut(ORMModel):
    id: int
    type: str | None
    method_key: str | None
    concise_description: str | None
    status: str
    fields: list[CollectedField] = []


class ConversationOut(ORMModel):
    id: int
    customer_id: int
    channel: str
    subject: str | None
    status: str
    language_code: str | None = None
    pending_field: str | None = None
    created_at: datetime
    updated_at: datetime
    complaint: ComplaintOut | None = None
    messages: list[MessageOut] = []


class IntakeResult(BaseModel):
    conversation: ConversationOut
    reply_body: str | None
    complaint_type: str | None = None
    method_key: str | None = None
    missing_fields: list[str]
    invalid_fields: list[str] = []
    awaiting_clarification: bool = False
    is_complete: bool
    ticket_reference: str | None = None
