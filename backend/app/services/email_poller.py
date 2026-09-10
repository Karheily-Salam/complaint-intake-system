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
        """Process one batch of new mail. Returns how many emails succeeded."""
        try:
            inbox = await self.provider.fetch_new()
        except Exception:
            # Network/auth failure: nothing was consumed, so simply try again
            # next interval. The message (not the credentials) is logged.
            logger.exception("Failed to fetch new email; will retry next poll")
            return 0

        processed = 0
        for inbound in inbox:
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

        try:
            await self.provider.mark_processed(inbound.message_id)
        except Exception:
            # The turn is safely committed; failing to flag the message only
            # means it may be fetched again, which the idempotency check
            # absorbs. Not an error worth failing the batch over.
            logger.warning("Could not acknowledge email %s with the provider", inbound.message_id)
        return True

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
