from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.email.base import MAX_BODY_CHARS, MAX_SUBJECT_CHARS
from app.schemas.common import ORMModel
from app.schemas.conversation import ConversationOut


class CustomerOut(ORMModel):
    id: int
    email: str
    name: str | None


class TicketSummary(ORMModel):
    id: int
    reference: str
    type: str
    status: str
    priority: str
    title: str
    concise_description: str
    created_at: datetime
    updated_at: datetime
    customer: CustomerOut


class TicketDetail(TicketSummary):
    structured_data: dict[str, Any]
    conversation: ConversationOut | None = None


class TicketStatusUpdate(BaseModel):
    status: str


class TicketReplyIn(BaseModel):
    """A support agent's reply.

    There is deliberately no recipient field: the address is read from the
    ticket on the server, so this endpoint cannot be used to send mail to an
    arbitrary destination. The subject is optional - omitted, the server
    builds the correctly threaded one.
    """

    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    subject: str | None = Field(default=None, max_length=MAX_SUBJECT_CHARS)


class TicketReplyOut(BaseModel):
    to_addr: str
    subject: str
    provider: str
    # True when the provider only simulated the send (mock). The dashboard
    # shows this verbatim rather than assuming a successful call means the
    # customer received anything.
    simulated: bool
    delivered: bool
    detail: str
    sent_at: datetime
