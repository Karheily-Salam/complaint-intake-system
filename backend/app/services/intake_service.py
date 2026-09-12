"""Orchestrates a single intake step: inbound email -> engine -> reply / ticket.

This is the only place that wires together the email provider, the conversation
engine and the database. The engine itself stays pure and stateless.

Two entry points, one shared core:

- :meth:`handle_inbound` - the local ``POST /inbox`` development simulator,
  where the caller states which conversation to continue.
- :meth:`handle_inbound_email` - a real email fetched from a mailbox by
  :class:`~app.services.email_poller.EmailPoller`, where the conversation is
  resolved from the message's own threading headers.

Both run the identical engine/persistence/ticket path; only conversation
resolution and the transport details of the outgoing reply differ.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.conversation.engine import ConversationEngine
from app.conversation.state import CollectedField, ConversationState
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.complaint import Complaint
from app.db.models.complaint_field import ComplaintField
from app.db.models.conversation import Conversation
from app.db.models.customer import Customer
from app.db.models.email_log import EmailLog
from app.db.models.ticket import Ticket
from app.domain.complaint_schemas.registry import get_registry
from app.domain.enums import (
    ComplaintStatus,
    ConversationStatus,
    FieldSource,
    FieldStatus,
    MessageDirection,
    TicketStatus,
)
from app.email.base import InboundEmail, OutboundEmail
from app.email.factory import get_demo_email_provider, get_email_provider
from app.email.quoting import strip_quoted_reply
from app.ml.prediction_log import record_classification, record_extraction
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.customer_repo import CustomerRepository
from app.schemas.conversation import InboundEmailIn, IntakeResult
from app.services.email_threading import (
    SUBJECT_REF_RE,
    subject_without_thread_token,
    thread_subject,
)
from app.services.ticket_service import TicketService

logger = get_logger(__name__)

# Matches the opaque thread reference embedded in every outbound subject line.
# Defined in app.services.email_threading so a staff reply threads identically.
_SUBJECT_REF_RE = SUBJECT_REF_RE


class IntakeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.customers = CustomerRepository(db)
        self.conversations = ConversationRepository(db)
        self.registry = get_registry()
        provider = get_ai_provider()
        self.engine = ConversationEngine(provider, self.registry)
        self.engine_provider_name = provider.name
        self.email = get_email_provider()

    # ------------------------------------------------------------------ entry points

    async def handle_inbound(self, payload: InboundEmailIn) -> IntakeResult:
        """Public demo path (``POST /inbox``) - no real mailbox involved.

        This endpoint is unauthenticated, and the sender address it is given is
        unverified, so it is confined to demo conversations: it can only create
        them and only continue them. A real (email-originated) conversation is
        excluded by the query, so claiming a victim's address and guessing a
        conversation id cannot reach their data.
        """
        # Customer identity is the inbound email address only - never reconciled
        # against user_id or other values from the message body. Scoped to the
        # demo side, so a caller claiming a real customer's address gets a
        # separate demo record rather than that customer's row.
        customer = self.customers.get_or_create(
            payload.from_addr, payload.customer_name, is_demo=True
        )

        if payload.conversation_id:
            conversation = self.conversations.get(payload.conversation_id, demo_only=True)
            if conversation is None:
                raise ValueError(f"Conversation {payload.conversation_id} not found")
            if conversation.customer_id != customer.id:
                raise ValueError("Conversation does not belong to this sender")
        else:
            conversation = self.conversations.create(
                customer_id=customer.id,
                subject=payload.subject or "Customer complaint",
                is_demo=True,
            )

        inbound_msg = self.conversations.add_message(
            conversation,
            direction=MessageDirection.INBOUND,
            sender=payload.from_addr,
            recipient=settings.support_inbox_address,
            subject=payload.subject,
            body=payload.body,
        )
        self._log_email(
            conversation,
            "inbound",
            payload.from_addr,
            settings.support_inbox_address,
            payload.subject,
            payload.body,
        )

        complaint = self.conversations.get_or_create_complaint(conversation)
        outcome = await self._run_turn(
            conversation, complaint, customer, payload.body, inbound_msg.id
        )
        reply_body, ticket, ticket_is_new = await self._finalize(
            conversation, customer, complaint, outcome
        )

        if reply_body:
            reply_subject = (
                f"Re: {conversation.subject}" if conversation.subject else "Re: Your complaint"
            )
            await self._send_and_record_reply(
                conversation, customer.email, reply_subject, reply_body
            )
        if ticket is not None and ticket_is_new:
            await self._send_ticket_notification(conversation, customer, complaint, ticket)

        self.db.commit()
        return self._result(conversation.id, outcome, reply_body, ticket)

    async def handle_inbound_email(self, inbound: InboundEmail) -> IntakeResult | None:
        """Real inbound email path.

        Returns ``None`` when the email was already processed (idempotency) -
        the caller should still acknowledge it with the provider.

        Everything below runs in a single transaction that is committed only
        once the reply has actually been handed to the email provider. A
        provider failure therefore rolls the whole turn back and the message
        is never acknowledged, so the next poll retries it cleanly rather than
        leaving a conversation advanced but the customer never asked.
        """
        if self.conversations.find_by_external_message_id(inbound.message_id) is not None:
            logger.info("Inbound email already processed, skipping: %s", inbound.message_id)
            return None

        customer = self.customers.get_or_create(inbound.from_addr, is_demo=False)
        conversation = self._resolve_conversation(customer, inbound)

        inbound_msg = self.conversations.add_message(
            conversation,
            direction=MessageDirection.INBOUND,
            sender=inbound.from_addr,
            recipient=inbound.to_addr,
            subject=inbound.subject,
            body=inbound.body,
            external_message_id=inbound.message_id,
            raw_meta={
                "in_reply_to": inbound.in_reply_to,
                "references": inbound.references,
                "received_at": inbound.received_at.isoformat(),
            },
        )
        self._log_email(
            conversation,
            "inbound",
            inbound.from_addr,
            inbound.to_addr,
            inbound.subject,
            inbound.body,
        )

        complaint = self.conversations.get_or_create_complaint(conversation)
        outcome = await self._run_turn(
            conversation, complaint, customer, inbound.body, inbound_msg.id,
            received_at=inbound.received_at,
        )
        reply_body, ticket, ticket_is_new = await self._finalize(
            conversation, customer, complaint, outcome
        )

        if reply_body:
            await self._send_and_record_reply(
                conversation,
                customer.email,
                self._thread_subject(conversation),
                reply_body,
                in_reply_to=inbound.message_id,
                references=_reply_references(inbound),
            )
        if ticket is not None and ticket_is_new:
            await self._send_ticket_notification(conversation, customer, complaint, ticket)

        self.db.commit()
        logger.info(
            "Processed inbound email for conversation %s (complete=%s)",
            conversation.id,
            outcome.is_complete,
        )
        return self._result(conversation.id, outcome, reply_body, ticket)

    # ------------------------------------------------------------------ shared core

    async def _run_turn(
        self,
        conversation: Conversation,
        complaint: Complaint,
        customer: Customer,
        body: str,
        source_message_id: int,
        *,
        received_at: datetime | None = None,
    ):
        """Run the engine for one message and persist everything it decided.

        The engine and the AI layer see the message with quoted reply history
        removed; the raw body stays exactly as received in the message row for
        audit and for the dashboard's conversation view. Cleaning here rather
        than at storage time is deliberate: one choke point, and nothing that
        has already been persisted is ever rewritten.
        """
        state = ConversationState(
            latest_message=strip_quoted_reply(body),
            complaint_type=complaint.type,
            collected=[
                CollectedField(key=f.key, value=f.value, status=_status(f.status))
                for f in complaint.fields
            ],
            customer_name=customer.name,
            inbound_transcript=[
                strip_quoted_reply(b) for b in self.conversations.inbound_bodies(conversation)
            ],
            language_code=conversation.language_code,
            pending_field=conversation.pending_field,
            reference_date=received_at.date() if received_at else None,
        )

        outcome = await self.engine.advance(state)
        conversation.language_code = outcome.language_code
        conversation.pending_field = outcome.pending_field

        if outcome.classification is not None:
            record_classification(
                self.db,
                conversation_id=conversation.id,
                message_id=source_message_id,
                is_demo=conversation.is_demo,
                language_code=outcome.language_code,
                classification=outcome.classification,
                final_type=outcome.complaint_type,
                provider_name=self.engine_provider_name,
            )
        if outcome.extraction is not None:
            record_extraction(
                self.db,
                conversation_id=conversation.id,
                message_id=source_message_id,
                is_demo=conversation.is_demo,
                language_code=outcome.language_code,
                audit=outcome.extraction,
                provider_name=self.engine_provider_name,
            )

        if outcome.complaint_type and not complaint.type:
            complaint.type = outcome.complaint_type
        complaint.method_key = (outcome.method_key or None) and outcome.method_key[:50]
        if outcome.concise_description:
            complaint.concise_description = outcome.concise_description

        self._persist_fields(complaint, outcome, source_message_id)

        if outcome.is_complete:
            complaint.status = ComplaintStatus.READY
        elif complaint.type:
            complaint.status = ComplaintStatus.COLLECTING
        conversation.status = outcome.next_status
        return outcome

    async def _finalize(
        self,
        conversation: Conversation,
        customer: Customer,
        complaint: Complaint,
        outcome,
    ) -> tuple[str | None, Ticket | None, bool]:
        """Create/refresh the ticket when complete and produce the reply body."""
        if not outcome.is_complete:
            return (outcome.reply.body if outcome.reply else None), None, False

        ticket_service = TicketService(self.db)
        is_new = complaint.ticket is None
        if is_new:
            ticket = ticket_service.create_for_complaint(conversation, complaint)
        else:
            ticket = ticket_service.refresh_snapshot(complaint.ticket, complaint)
        conversation.status = ConversationStatus.COMPLETED

        # A completed thread stays open to the customer, and people reply to
        # it - "thanks", an out-of-band question, a corrected value. Only the
        # last of those is worth another confirmation. The test is whether the
        # engine actually changed any collected field this turn (a persisted,
        # deterministic signal from extraction + validation), never how many
        # messages exist or what the text looked like: restating the same
        # value or thanking us changes nothing and warrants no email.
        if not is_new and not any(field.changed for field in outcome.fields):
            logger.info(
                "Conversation %s already ticketed (%s) and this reply changed no "
                "collected data - not resending the confirmation",
                conversation.id,
                ticket.reference,
            )
            return None, ticket, False

        # Only now does the real reference exist, so the confirmation is
        # composed here rather than inside ConversationEngine.advance.
        reply = await self.engine.compose_ticket_confirmation(
            outcome, customer.name, ticket.reference
        )
        return reply.body, ticket, is_new

    # ------------------------------------------------------------------ threading

    def _resolve_conversation(self, customer: Customer, inbound: InboundEmail) -> Conversation:
        """Find the conversation this email belongs to, or start a new one.

        Order of preference:
        1. RFC 5322 threading headers (``In-Reply-To``, then ``References``
           newest-first) matched against the Message-ID of an email we sent.
        2. The opaque ``[Ref:...]`` token in the subject - the fallback for
           providers that rewrite Message-IDs in transit.

        The sender's address is deliberately never used on its own to pick a
        conversation: one customer may have several complaints open at once.

        Whatever a signal resolves to is still subject to _is_threadable, so a
        match that is not allowed to be continued falls through to a brand-new
        conversation at the bottom of this method.
        """
        candidates = [inbound.in_reply_to, *reversed(inbound.references)]
        for external_id in candidates:
            if not external_id:
                continue
            message = self.conversations.find_by_external_message_id(external_id)
            if message is None:
                continue
            conversation = self.conversations.get(message.conversation_id)
            if self._is_threadable(conversation, customer):
                return conversation  # type: ignore[return-value]

        token_match = _SUBJECT_REF_RE.search(inbound.subject or "")
        if token_match:
            conversation = self.conversations.get_by_thread_token(token_match.group(1).lower())
            if self._is_threadable(conversation, customer):
                return conversation  # type: ignore[return-value]

        # The customer keeps their identity; only the thread is new. Any
        # inherited [Ref:...] token is stripped so this conversation gets its
        # own - see subject_without_thread_token.
        return self.conversations.create(
            customer_id=customer.id,
            subject=subject_without_thread_token(inbound.subject),
        )

    @staticmethod
    def _is_threadable(conversation: Conversation | None, customer: Customer) -> bool:
        """Whether real inbound mail may continue this conversation.

        Applied to whatever the threading signals resolved to, so every rule
        here holds however the match was made - In-Reply-To, the References
        chain, or the subject token. That is deliberate: the lifecycle state
        outranks the email metadata, rather than each call site having to
        remember to check.

        Real email never joins a demo conversation, even if a crafted header
        or subject token points at one - the two data sets stay separate in
        both directions.

        A conversation whose ticket support has closed is never continued
        either. Closing a ticket ends that matter, so a later email is a new
        matter even when the customer simply replied to the old thread.
        Returning False sends _resolve_conversation on to create a fresh
        conversation, which gets its own history and its own ticket reference,
        while the closed ticket and its history are left untouched.
        """
        if conversation is None or conversation.customer_id != customer.id:
            return False
        if conversation.is_demo:
            return False

        ticket = conversation.ticket
        if ticket is not None and ticket.status == TicketStatus.CLOSED:
            logger.info(
                "Inbound email threaded to conversation %s, whose ticket %s is closed - "
                "starting a new ticket instead of reopening it",
                conversation.id,
                ticket.reference,
            )
            return False
        return True

    @staticmethod
    def _thread_subject(conversation: Conversation) -> str:
        return thread_subject(conversation)

    # ------------------------------------------------------------------ sending

    def _transport(self, conversation: Conversation):
        """The email provider this conversation may use.

        Real conversations use the configured transport. Demo conversations -
        created by anyone through the public ``POST /inbox`` with an unverified
        sender address - are confined to a simulated one, so the demo can never
        make production send mail to an address someone chose (see
        app.email.factory.get_demo_email_provider). Locally, where the
        configured provider is already the mock, this is the same object.
        """
        return get_demo_email_provider() if conversation.is_demo else self.email

    async def _send_and_record_reply(
        self,
        conversation: Conversation,
        to_addr: str,
        subject: str,
        body: str,
        *,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
    ) -> None:
        sent = await self._transport(conversation).send(
            OutboundEmail(
                to_addr=to_addr,
                from_addr=settings.smtp_sender,
                subject=subject,
                body=body,
                in_reply_to=in_reply_to,
                references=references or [],
                thread_id=str(conversation.id),
            )
        )
        # Persisting the Message-ID we sent is what lets the customer's reply
        # be threaded back to this conversation.
        self.conversations.add_message(
            conversation,
            direction=MessageDirection.OUTBOUND,
            sender=settings.smtp_sender,
            recipient=to_addr,
            subject=subject,
            body=body,
            external_message_id=sent.message_id,
        )
        self._log_email(conversation, "outbound", settings.smtp_sender, to_addr, subject, body)

    async def _send_ticket_notification(
        self,
        conversation: Conversation,
        customer: Customer,
        complaint: Complaint,
        ticket: Ticket,
    ) -> None:
        """Send the completed, structured ticket to the support/admin inbox.

        Internal-facing and deterministic: the only AI-derived content is the
        stored concise description. Not added to the customer conversation
        thread - it is a separate internal notification.
        """
        subject = f"[Ticket {ticket.reference}] {ticket.title}"
        body = self._ticket_notification_body(conversation, customer, complaint, ticket)
        await self._transport(conversation).send(
            OutboundEmail(
                to_addr=settings.support_inbox_address,
                from_addr=settings.smtp_sender,
                subject=subject,
                body=body,
            )
        )
        self._log_email(
            conversation,
            "outbound",
            settings.smtp_sender,
            settings.support_inbox_address,
            subject,
            body,
        )

    def _ticket_notification_body(
        self,
        conversation: Conversation,
        customer: Customer,
        complaint: Complaint,
        ticket: Ticket,
    ) -> str:
        schema = self.registry.try_get(complaint.type)
        type_label = schema.label if schema else (complaint.type or "Complaint")
        labels = {spec.key: spec.label for spec in (schema.fields_for() if schema else [])}
        values = {f.key: f.value for f in complaint.fields if f.value}

        lines = [
            f"Ticket reference: {ticket.reference}",
            f"Complaint type:   {type_label}",
            f"Customer:         {customer.name or '(name not provided)'} <{customer.email}>",
            f"Language:         {conversation.language_code or 'unknown'}",
            f"Messages:         {len(conversation.messages)}",
            f"Opened:           {conversation.created_at:%Y-%m-%d %H:%M} UTC",
            "",
            "Collected information",
            "---------------------",
        ]
        collected_any = False
        for key, value in values.items():
            if key == "problem_description":
                continue
            lines.append(f"  {labels.get(key, key)}: {value}")
            collected_any = True
        if not collected_any:
            lines.append("  (none)")

        description = values.get("problem_description")
        if description:
            lines += ["", "Problem description (customer's own words)", "-" * 42, description]

        if complaint.concise_description and complaint.concise_description != description:
            lines += ["", "Summary", "-------", complaint.concise_description]

        lines += [
            "",
            "Reply to the customer directly at their address above; this "
            "mailbox is monitored automatically.",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ helpers

    def _persist_fields(self, complaint: Complaint, outcome, source_message_id: int) -> None:
        existing = {f.key: f for f in complaint.fields}
        for fo in outcome.fields:
            row = existing.get(fo.key)
            if row is None:
                row = ComplaintField(complaint_id=complaint.id, key=fo.key)
                complaint.fields.append(row)
                existing[fo.key] = row
            elif not fo.changed:
                continue

            row.value = fo.value
            row.status = _status(fo.status)
            row.source = FieldSource.CUSTOMER_MESSAGE
            row.validation_error = fo.validation_error
            if fo.confidence is not None:
                row.confidence = fo.confidence
            if fo.changed:
                row.source_message_id = source_message_id
                # A new value never keeps the previous value's evidence.
                ev = fo.evidence
                row.evidence_text = ev.text if ev else None
                row.evidence_start = ev.start if ev else None
                row.evidence_end = ev.end if ev else None
                row.evidence_method = ev.method if ev else None

    def _result(
        self,
        conversation_id: int,
        outcome,
        reply_body: str | None,
        ticket: Ticket | None,
    ) -> IntakeResult:
        fresh = self.conversations.get(conversation_id)
        return IntakeResult(
            conversation=fresh,  # type: ignore[arg-type]
            reply_body=reply_body,
            complaint_type=outcome.complaint_type,
            method_key=outcome.method_key,
            missing_fields=[f.key for f in outcome.missing_fields],
            invalid_fields=[f.key for f in outcome.invalid_fields],
            awaiting_clarification=outcome.awaiting_clarification,
            is_complete=outcome.is_complete,
            ticket_reference=ticket.reference if ticket else None,
        )

    def _log_email(self, conversation, direction, from_addr, to_addr, subject, body) -> None:
        self.db.add(
            EmailLog(
                conversation_id=conversation.id,
                direction=direction,
                # The provider that actually handled it, which for a demo
                # conversation is the simulated one whatever is configured.
                provider=self._transport(conversation).name,
                from_addr=from_addr,
                to_addr=to_addr,
                subject=subject,
                body=body,
            )
        )


def _reply_references(inbound: InboundEmail) -> list[str]:
    """Build the References chain for a reply to ``inbound`` (RFC 5322)."""
    references = list(inbound.references)
    if inbound.message_id not in references:
        references.append(inbound.message_id)
    return references


def _status(value: str | FieldStatus) -> FieldStatus:
    return value if isinstance(value, FieldStatus) else FieldStatus(value)
