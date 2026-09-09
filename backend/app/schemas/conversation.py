from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel
from app.schemas.message import MessageOut


class InboundEmailIn(BaseModel):
    """Simulated inbound customer email (what MockEmailProvider would receive)."""

    from_addr: EmailStr
    body: str = Field(min_length=1)
    subject: str | None = None
    customer_name: str | None = None
    # Continue an existing conversation; omit to start a new one.
    conversation_id: int | None = None


class CollectedField(ORMModel):
    key: str
    value: str | None
    status: str
    source: str
    confidence: float | None
    validation_error: str | None


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
    created_at: datetime
    updated_at: datetime
    complaint: ComplaintOut | None = None
    messages: list[MessageOut] = []


class IntakeResult(BaseModel):
    conversation: ConversationOut
    reply_body: str | None
    missing_fields: list[str]
    is_complete: bool
    ticket_reference: str | None = None
