"""Email provider abstraction.

The conversation/intake layer speaks only to :class:`EmailProvider`. Swapping
``MockEmailProvider`` for a real ``ImapSmtpEmailProvider`` (or a future Gmail
API / Microsoft Graph provider) must not require engine or service changes.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

# Every value below arrives from a stranger's mail client. Bounds are applied
# here, at the model, rather than in one provider, so any transport (IMAP now,
# a webhook later) is covered by construction.
#
# Sizes are chosen to fit the database columns that store them
# (messages.subject 300, messages.external_message_id 500, customers.email
# 320): SQLite silently accepts over-long values, PostgreSQL raises, and a
# truncation bug that only appears after a database swap is worth avoiding.
MAX_SUBJECT_CHARS = 200          # leaves room for "Re: " + " [Ref:token]" in a reply
MAX_BODY_CHARS = 100_000         # ~100KB of text is far beyond any real complaint
MAX_ADDRESS_CHARS = 320
MAX_MESSAGE_ID_CHARS = 500
MAX_REFERENCES = 50

# Control characters, most importantly CR/LF. A header value containing one is
# rejected outright by Python's email package when we later build a reply -
# which would make send() raise every time, so the message could never be
# acknowledged and would be retried forever. A subject can genuinely carry one
# despite RFC 5322 folding, because an encoded-word decodes *after* unfolding
# (e.g. "Subject: =?utf-8?B?<base64 with a newline>?=").
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_BREAKS = re.compile(r"[\r\n]+")


def header_safe(value: str | None, *, limit: int) -> str | None:
    """Make an untrusted header value safe to store and to echo into a reply."""
    if value is None:
        return None
    cleaned = _LINE_BREAKS.sub(" ", value)
    cleaned = _CONTROL_CHARS.sub("", cleaned).strip()
    return cleaned[:limit]


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
    # RFC 3834 ``Auto-Submitted`` and the legacy ``Precedence`` header. An
    # automatic responder must not reply to automatic mail (out-of-office
    # notices, bounces, list traffic) - see EmailPoller._loop_risk.
    auto_submitted: str | None = None
    precedence: str | None = None
    # Deprecated free-form correlation id used only by the mock/dev HTTP path
    # (``/inbox``). Real providers use message_id/in_reply_to/references
    # instead - never rely on this for real email threading.
    thread_id: str | None = None

    @field_validator("subject", "auto_submitted", "precedence")
    @classmethod
    def _clean_short_header(cls, value: str | None) -> str | None:
        return header_safe(value, limit=MAX_SUBJECT_CHARS)

    @field_validator("message_id", "in_reply_to")
    @classmethod
    def _clean_message_id(cls, value: str | None) -> str | None:
        return header_safe(value, limit=MAX_MESSAGE_ID_CHARS)

    @field_validator("from_addr", "to_addr")
    @classmethod
    def _clean_address(cls, value: str) -> str:
        return header_safe(value, limit=MAX_ADDRESS_CHARS) or ""

    @field_validator("references")
    @classmethod
    def _clean_references(cls, value: list[str]) -> list[str]:
        # A References chain grows with every hop and is attacker-controlled;
        # each entry costs a database lookup during threading, so both the
        # entries and the length of the chain are bounded.
        cleaned = [header_safe(item, limit=MAX_MESSAGE_ID_CHARS) for item in value]
        return [item for item in cleaned if item][:MAX_REFERENCES]

    @field_validator("body")
    @classmethod
    def _bound_body(cls, value: str) -> str:
        # Keeps a huge (or maliciously padded) message from being summarised,
        # re-summarised on every later turn, and stored in full.
        if len(value) <= MAX_BODY_CHARS:
            return value
        return value[:MAX_BODY_CHARS] + "\n\n[message truncated]"


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

    @field_validator("subject")
    @classmethod
    def _clean_subject(cls, value: str) -> str:
        # Also covers a reply built from a conversation stored before inbound
        # sanitising existed, so a legacy row cannot wedge the poller.
        return header_safe(value, limit=MAX_SUBJECT_CHARS + 100) or "Your complaint"


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
    # True when send() only records the message instead of handing it to a
    # real mail server. The support dashboard reports delivery from this, so a
    # simulated send is never presented to an agent as a delivered email.
    # Declared on the abstraction rather than inferred from ``name``, so a
    # future provider (a sandbox/staging transport, say) states its own answer.
    is_simulated: bool = False

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

    async def wait_for_activity(self, timeout: float) -> bool:
        """Block until new mail may have arrived, or until ``timeout`` elapses.

        This is a *trigger only*. Returning True means "something may have
        changed, look now" - never what changed. The caller still decides what
        to fetch, so a spurious wake-up costs one empty search and a missed
        notification is caught by the timeout. Both failure directions are
        therefore safe, which is what allows an aggressive implementation.

        The default simply waits, which is exactly the fixed-interval polling
        behaviour every provider had before: overriding this is an
        optimisation, never a requirement.
        """
        await asyncio.sleep(timeout)
        return False

    async def shutdown(self) -> None:
        """Release any long-lived resources. Default: nothing to release."""
        return None

    async def mark_processed(self, message_id: str) -> None:
        """Acknowledge that ``message_id`` was successfully persisted.

        Only call this AFTER the corresponding database transaction has
        committed - never before, so a crash between fetch and commit results
        in the message being retried rather than silently dropped. Default is
        a no-op (fine for a provider whose ``fetch_new`` already only returns
        each item once, such as the mock).
        """
        return None
