"""Email provider abstraction.

The conversation/intake layer speaks only to :class:`EmailProvider`. Swapping
``MockEmailProvider`` for a real ``GmailEmailProvider`` /
``MicrosoftGraphEmailProvider`` must not require engine changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class InboundEmail(BaseModel):
    message_id: str
    from_addr: str
    to_addr: str
    subject: str | None = None
    body: str
    received_at: datetime = Field(default_factory=_now)
    thread_id: str | None = None


class OutboundEmail(BaseModel):
    to_addr: str
    subject: str
    body: str
    from_addr: str | None = None
    thread_id: str | None = None


class SentEmail(BaseModel):
    message_id: str
    to_addr: str
    subject: str
    sent_at: datetime = Field(default_factory=_now)
    thread_id: str | None = None


class EmailProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def fetch_new(self) -> list[InboundEmail]:
        """Return unprocessed inbound emails (polling model)."""

    @abstractmethod
    async def send(self, email: OutboundEmail) -> SentEmail:
        """Send an outbound email and return delivery metadata."""
