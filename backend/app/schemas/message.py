from __future__ import annotations

from datetime import datetime

from app.schemas.common import ORMModel


class MessageOut(ORMModel):
    id: int
    direction: str
    sender: str
    recipient: str | None
    subject: str | None
    body: str
    created_at: datetime
