"""Email provider abstraction.

The conversation/intake layer speaks only to :class:`EmailProvider`. Swapping
``MockEmailProvider`` for a real ``ImapSmtpEmailProvider`` (or a future Gmail
API / Microsoft Graph provider) must not require engine or service changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class InboundEmail(BaseModel):
    # The email's own RFC 5322 ``Message-ID`` header (globally unique per
    # message). This is the external identifier persisted for idempotency and
    # threading lookups - never fabricate one for a real message.
    message_id: str
    from_addr: str
    to_addr: str
    subject: str | None = None
    body: str
    received_at: datetime = Field(default_factory=_now)
    # RFC 5322 ``In-Reply-To`` header of the message being replied to, if any.
    in_reply_to: str | None = None
    # RFC 5322 ``References`` header, oldest first, if any.
    references: list[str] = Field(default_factory=list)
    # Deprecated free-form correlation id used only by the mock/dev HTTP path
    # (``/inbox``). Real providers use message_id/in_reply_to/references
    # instead - never rely on this for real email threading.
    thread_id: str | None = None


class OutboundEmail(BaseModel):
    to_addr: str
    subject: str
    body: str
    from_addr: str | None = None
    # Set when replying to a specific inbound message, so a real provider can
    # populate the ``In-Reply-To``/``References`` headers correctly.
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    # See InboundEmail.thread_id - mock/dev only.
    thread_id: str | None = None


class SentEmail(BaseModel):
    # The ``Message-ID`` the provider actually sent with (ours if the
    # provider preserves it, otherwise whatever it assigned) - persisted so a
    # later reply's In-Reply-To/References can be matched back to it.
    message_id: str
    to_addr: str
    subject: str
    sent_at: datetime = Field(default_factory=_now)
    thread_id: str | None = None


class EmailProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def fetch_new(self) -> list[InboundEmail]:
        """Return unprocessed inbound emails (polling model).

        Must NOT mark returned messages as processed - see
        :meth:`mark_processed`. A message may be returned again on a later
        call if it was never acknowledged (e.g. the previous run crashed
        before persisting it), so callers must be idempotent.
        """

    @abstractmethod
    async def send(self, email: OutboundEmail) -> SentEmail:
        """Send an outbound email and return delivery metadata."""

    async def mark_processed(self, message_id: str) -> None:
        """Acknowledge that ``message_id`` was successfully persisted.

        Only call this AFTER the corresponding database transaction has
        committed - never before, so a crash between fetch and commit results
        in the message being retried rather than silently dropped. Default is
        a no-op (fine for a provider whose ``fetch_new`` already only returns
        each item once, such as the mock).
        """
        return None
