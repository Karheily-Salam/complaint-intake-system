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

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.email.base import EmailProvider
from app.email.factory import get_email_provider
from app.services.intake_service import IntakeService

logger = get_logger(__name__)


class EmailPoller:
    def __init__(self, provider: EmailProvider | None = None, session_factory=SessionLocal) -> None:
        self._provider = provider
        self._session_factory = session_factory

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
        try:
            inbox = await self.provider.fetch_new()
        except Exception:
            # Network/auth failure: nothing was consumed, so simply try again
            # next interval. The message (not the credentials) is logged.
            logger.exception("Failed to fetch new email; will retry next poll")
            return 0

        processed = 0
        for inbound in inbox:
            skip_reason = _loop_risk(inbound)
            if skip_reason:
                # Acknowledged, not processed: it is answered/ignored on
                # purpose, so leaving it unread would just re-trigger this
                # every poll forever.
                logger.info("Ignoring %s email %s", skip_reason, inbound.message_id)
                await self._acknowledge(inbound)
                continue
            if await self._process_one(inbound):
                processed += 1
        return processed

    async def _process_one(self, inbound) -> bool:
        db = self._session_factory()
        try:
            await IntakeService(db).handle_inbound_email(inbound)
        except Exception:
            db.rollback()
            # Deliberately NOT acknowledged: the email stays in the mailbox and
            # is retried on the next poll. Logged without the body, which can
            # contain personal data.
            logger.exception(
                "Failed to process inbound email %s; left for retry", inbound.message_id
            )
            return False
        finally:
            db.close()

        await self._acknowledge(inbound)
        return True

    async def _acknowledge(self, inbound) -> None:
        try:
            await self.provider.mark_processed(inbound.message_id)
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
