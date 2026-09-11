"""A support agent's reply to the customer on a ticket.

Reuses the existing :class:`EmailProvider` abstraction rather than introducing
a second way to send mail: the same transport, the same threading headers, and
the same message table as the engine's automated replies, so a manual reply
appears in the conversation history and the customer's answer threads back
into the same conversation.

Two properties are deliberate and load-bearing:

* the recipient is read from the ticket, never from the request body, so a
  staff account cannot be used to send mail to an arbitrary address;
* delivery is reported from the provider's own ``is_simulated`` flag, so an
  agent is never told a real email went out when the mock provider is active.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.ticket import Ticket
from app.domain.enums import MessageDirection
from app.email.base import OutboundEmail
from app.email.factory import get_email_provider
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.ticket_repo import TicketRepository
from app.services.email_threading import reply_references, reply_to_message, thread_subject

logger = get_logger(__name__)


class ReplyNotPossible(RuntimeError):
    """This ticket cannot be replied to. The message is shown to the agent."""


@dataclass(frozen=True)
class ReplyResult:
    to_addr: str
    subject: str
    provider: str
    simulated: bool
    delivered: bool
    detail: str
    sent_at: datetime


class TicketReplyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tickets = TicketRepository(db)
        self.conversations = ConversationRepository(db)
        self.email = get_email_provider()

    async def reply(
        self, reference: str, body: str, subject: str | None = None
    ) -> ReplyResult | None:
        """Send ``body`` to the ticket's customer. None if no such ticket."""
        ticket = self.tickets.get_by_reference(reference)
        if ticket is None:
            return None

        conversation = ticket.conversation
        if conversation is None:
            raise ReplyNotPossible(
                "This ticket has no email conversation, so there is no thread to reply to."
            )

        # A demo ticket's customer address is synthetic (example.com). Sending
        # real mail to it would be a bounce at best and mail to a stranger at
        # worst, so the real transport refuses; the mock one is free to accept.
        if conversation.is_demo and not self.email.is_simulated:
            raise ReplyNotPossible(
                "This is a demo ticket with a synthetic customer address. "
                "Refusing to send a real email to it."
            )

        to_addr = self._recipient(ticket)
        if not to_addr:
            raise ReplyNotPossible("This ticket has no customer email address to reply to.")

        full_subject = thread_subject(conversation, subject)
        in_reply_to = reply_to_message(conversation)

        sent = await self.email.send(
            OutboundEmail(
                to_addr=to_addr,
                from_addr=settings.smtp_sender,
                subject=full_subject,
                body=body,
                in_reply_to=in_reply_to.external_message_id if in_reply_to else None,
                references=reply_references(conversation),
                thread_id=str(conversation.id),
            )
        )

        # Recorded on the conversation so the agent's reply is part of the
        # history, and so the customer's answer can be threaded back to it.
        self.conversations.add_message(
            conversation,
            direction=MessageDirection.OUTBOUND,
            sender=settings.smtp_sender,
            recipient=to_addr,
            subject=full_subject,
            body=body,
            external_message_id=sent.message_id,
            raw_meta={"origin": "staff_reply"},
        )
        self.db.commit()

        logger.info(
            "Staff reply sent on ticket %s via %s (simulated=%s)",
            ticket.reference,
            self.email.name,
            self.email.is_simulated,
        )
        return ReplyResult(
            to_addr=to_addr,
            subject=full_subject,
            provider=self.email.name,
            simulated=self.email.is_simulated,
            delivered=not self.email.is_simulated,
            detail=self._detail(to_addr),
            sent_at=sent.sent_at or datetime.now(UTC),
        )

    @staticmethod
    def _recipient(ticket: Ticket) -> str | None:
        """The reply address, always from stored ticket data."""
        return ticket.customer.email if ticket.customer else None

    def _detail(self, to_addr: str) -> str:
        if self.email.is_simulated:
            return (
                f"Message accepted by the {self.email.name} email provider and added to "
                "the conversation. No real email was delivered - this deployment is not "
                "configured with a live mail transport."
            )
        return f"Email sent to {to_addr} via the {self.email.name} provider."
