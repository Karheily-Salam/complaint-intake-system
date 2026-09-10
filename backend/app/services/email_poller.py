"""Background poller: mailbox -> IntakeService -> reply.

The customer only ever uses email. Nothing here is customer-facing and no web
form is involved: this loop is what makes the system actually *receive* mail.

Reliability contract (per email, independently):

1. ``fetch_new()`` returns messages the provider has not been told to
   acknowledge yet - it never marks them itself.
2. Each message is processed in its own database session/transaction, so one
   bad message can never affect another.
3. Only after that transaction commits is the message acknowledged with
   ``mark_processed()``. A crash or provider failure anywhere earlier leaves
   the message unacknowledged, so the next poll retries it - and the
   idempotency check in :meth:`IntakeService.handle_inbound_email` means a
   message that *was* persisted is never processed twice.

A fixed poll interval is the retry mechanism; there is deliberately no bespoke
backoff, which at this scale would be complexity without benefit.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.email.base import EmailProvider
from app.email.factory import get_email_provider
from app.services.intake_service import IntakeService

logger = get_logger(__name__)


@dataclass
class PollerHealth:
    """Whether the mailbox is actually being read.

    A poller that has silently stopped working looks identical to a quiet
    mailbox, so the answer to "is intake alive?" needs to be observable rather
    than inferred from the absence of complaints. Held in memory on purpose:
    it describes this process, resets with it, and is not worth a table.
    """

    last_poll_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    # Message only - never the exception's payload, which could quote mail.
    last_failure_reason: str | None = None
    consecutive_failures: int = 0
    total_polls: int = 0
    total_emails_processed: int = 0

    def record_start(self) -> None:
        self.last_poll_started_at = datetime.now(UTC)
        self.total_polls += 1

    def record_success(self, processed: int) -> None:
        self.last_success_at = datetime.now(UTC)
        self.consecutive_failures = 0
        self.total_emails_processed += processed

    def record_failure(self, reason: str) -> None:
        self.last_failure_at = datetime.now(UTC)
        self.last_failure_reason = reason
        self.consecutive_failures += 1

    def snapshot(self) -> dict:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "last_poll_started_at": iso(self.last_poll_started_at),
            "last_success_at": iso(self.last_success_at),
            "last_failure_at": iso(self.last_failure_at),
            "last_failure_reason": self.last_failure_reason,
            "consecutive_failures": self.consecutive_failures,
            "total_polls": self.total_polls,
            "total_emails_processed": self.total_emails_processed,
        }


# Process-wide, so the ops endpoint reports on the poller the app is running.
poller_health = PollerHealth()


class EmailPoller:
    def __init__(
        self,
        provider: EmailProvider | None = None,
        session_factory=SessionLocal,
        health: PollerHealth | None = None,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        # Defaults to the process-wide record the ops endpoint reads; tests
        # pass their own so they do not perturb it.
        self.health = health or poller_health

    @property
    def provider(self) -> EmailProvider:
        if self._provider is None:
            self._provider = get_email_provider()
        return self._provider

    async def poll_once(self) -> int:
        """Process one batch of new mail.

        Returns the number of emails handled without error, which includes one
        recognised as already processed and skipped - that is a success, not
        new work. Emails ignored as loop/auto-reply traffic are not counted,
        and neither are failures, which stay in the mailbox for the next poll.
        """
        self.health.record_start()
        started = time.monotonic()
        try:
            inbox = await self._with_timeout(self.provider.fetch_new(), "fetch")
        except Exception as exc:
            # Network/auth failure, or a provider that hung past the ceiling:
            # nothing was consumed, so simply try again next interval. The
            # error is logged (never the credentials) rather than swallowed.
            self.health.record_failure(type(exc).__name__)
            logger.exception(
                "poll=failed stage=fetch consecutive_failures=%d - will retry next poll",
                self.health.consecutive_failures,
            )
            return 0

        processed = 0
        for inbound in inbox:
            skip_reason = _loop_risk(inbound)
            if skip_reason:
                # Acknowledged, not processed: it is answered/ignored on
                # purpose, so leaving it unread would just re-trigger this
                # every poll forever.
                logger.info("email=%s action=ignored reason=%s", inbound.message_id, skip_reason)
                await self._acknowledge(inbound)
                continue
            if await self._process_one(inbound):
                processed += 1

        self.health.record_success(processed)
        logger.info(
            "poll=ok fetched=%d processed=%d duration_ms=%d",
            len(inbox),
            processed,
            int((time.monotonic() - started) * 1000),
        )
        return processed

    async def _process_one(self, inbound) -> bool:
        db = self._session_factory()
        started = time.monotonic()
        try:
            result = await IntakeService(db).handle_inbound_email(inbound)
        except Exception:
            db.rollback()
            # Deliberately NOT acknowledged: the email stays in the mailbox and
            # is retried on the next poll. Logged without the body, which can
            # contain personal data.
            logger.exception(
                "email=%s action=failed - left in the mailbox for retry", inbound.message_id
            )
            return False
        finally:
            db.close()

        # message_id is the correlation id across the whole pipeline: it
        # appears here, in the idempotency skip, and on the stored message.
        logger.info(
            "email=%s action=processed conversation=%s ticket=%s duration_ms=%d",
            inbound.message_id,
            result.conversation.id if result else "-",
            (result.ticket_reference if result else None) or "-",
            int((time.monotonic() - started) * 1000),
        )
        await self._acknowledge(inbound)
        return True

    async def _with_timeout(self, awaitable, what: str):
        """Cap any single provider operation.

        The IMAP/SMTP providers set their own socket timeouts, which is the
        real fix; this is the outer guarantee that one poll cycle cannot run
        forever even if a provider fails to honour its own - the poller must
        always come back round to try again.
        """
        ceiling = max(5, settings.email_operation_timeout_seconds)
        try:
            return await asyncio.wait_for(awaitable, timeout=ceiling)
        except TimeoutError:
            logger.error("Email provider '%s' exceeded %ss; abandoning this cycle", what, ceiling)
            raise

    async def _acknowledge(self, inbound) -> None:
        try:
            await self._with_timeout(
                self.provider.mark_processed(inbound.message_id), "acknowledge"
            )
        except Exception:
            # The turn is safely committed; failing to flag the message only
            # means it may be fetched again, which the idempotency check
            # absorbs. Not an error worth failing the batch over.
            logger.warning("Could not acknowledge email %s with the provider", inbound.message_id)

    async def run_forever(self) -> None:
        interval = max(5, settings.email_poll_interval_seconds)
        logger.info("Email poller started (every %ss, provider=%s)", interval, self.provider.name)
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                logger.info("Email poller stopped")
                raise
            except Exception:
                logger.exception("Unexpected error in email poll cycle")
            await asyncio.sleep(interval)


def our_own_addresses() -> set[str]:
    """Every address this system sends from or to internally."""
    candidates = (
        settings.imap_username,
        settings.smtp_from_addr,
        settings.smtp_sender,
        settings.support_inbox_address,
    )
    return {addr.strip().lower() for addr in candidates if addr}


def _loop_risk(inbound) -> str | None:
    """Why this email must not be answered, or None if it is genuine customer mail.

    An automatic responder that replies to other automatic mail creates a
    loop. Two cases matter here:

    - *Our own mail coming back*: the ticket notification we send to the
      support inbox lands in the polled mailbox if the two are the same
      address. Without this check the poller would read it as a new customer
      complaint from ourselves, reply to itself, and keep going - a runaway
      loop of tickets and mail from a single plausible misconfiguration.
    - *Auto-replies* (out-of-office, bounces, list mail): RFC 3834 requires an
      automatic responder not to answer these, and treating a vacation notice
      as the customer's answer to a question would corrupt the conversation.

    Both are skipped and acknowledged rather than left unread.
    """
    sender = (inbound.from_addr or "").strip().lower()
    if sender and sender in our_own_addresses():
        return "self-addressed"

    auto_submitted = (getattr(inbound, "auto_submitted", None) or "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return "auto-submitted"

    precedence = (getattr(inbound, "precedence", None) or "").strip().lower()
    if precedence in {"bulk", "list", "junk", "auto_reply"}:
        return f"{precedence}-precedence"

    return None
