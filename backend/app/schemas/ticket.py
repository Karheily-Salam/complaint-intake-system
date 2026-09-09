from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

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
