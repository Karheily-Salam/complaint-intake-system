"""Orchestrates a single intake step: inbound email -> engine -> reply / ticket.

This is the only place that wires together the email provider, the conversation
engine and the database. The engine itself stays pure.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.conversation.engine import ConversationEngine
from app.conversation.state import ConversationState
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.complaint_field import ComplaintField
from app.db.models.conversation import Conversation
from app.db.models.email_log import EmailLog
from app.domain.complaint_schemas.registry import get_registry
from app.domain.enums import (
    ComplaintStatus,
    ConversationStatus,
    FieldSource,
    FieldStatus,
    MessageDirection,
)
from app.domain.validation import validate_field
from app.email.base import OutboundEmail
from app.email.factory import get_email_provider
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.customer_repo import CustomerRepository
from app.schemas.conversation import InboundEmailIn, IntakeResult
from app.services.ticket_service import TicketService

logger = get_logger(__name__)


class IntakeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.customers = CustomerRepository(db)
        self.conversations = ConversationRepository(db)
        self.registry = get_registry()
        self.engine = ConversationEngine(get_ai_provider(), self.registry)
        self.email = get_email_provider()

    async def handle_inbound(self, payload: InboundEmailIn) -> IntakeResult:
        customer = self.customers.get_or_create(payload.from_addr, payload.customer_name)

        if payload.conversation_id:
            conversation = self.conversations.get(payload.conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {payload.conversation_id} not found")
        else:
            conversation = self.conversations.create(
                customer_id=customer.id,
                subject=payload.subject or "Customer complaint",
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
            conversation, "inbound", payload.from_addr, settings.support_inbox_address,
            payload.subject, payload.body,
        )

        complaint = self.conversations.get_or_create_complaint(conversation)

        state = ConversationState(
            latest_message=payload.body,
            complaint_type=complaint.type,
            method_key=complaint.method_key,
            collected={f.key: f.value for f in complaint.fields if f.value},
            customer_name=customer.name,
            inbound_transcript=self.conversations.inbound_bodies(conversation),
        )

        outcome = await self.engine.advance(state)

        # ---- persist engine outcome ----
        if outcome.complaint_type and not complaint.type:
            complaint.type = outcome.complaint_type
        if outcome.method_key:
            complaint.method_key = outcome.method_key
        if outcome.concise_description:
            complaint.concise_description = outcome.concise_description

        self._merge_fields(complaint, outcome, inbound_msg.id)

        complaint.status = (
            ComplaintStatus.READY if outcome.is_complete else ComplaintStatus.COLLECTING
        )
        conversation.status = outcome.next_status

        reply_body = outcome.reply.body if outcome.reply else None
        if reply_body:
            await self._send_reply(conversation, customer.email, conversation.subject, reply_body)

        ticket_reference: str | None = None
        if outcome.is_complete:
            ticket = TicketService(self.db).create_for_complaint(conversation, complaint)
            ticket_reference = ticket.reference
            conversation.status = ConversationStatus.COMPLETED

        self.db.commit()

        fresh = self.conversations.get(conversation.id)
        return IntakeResult(
            conversation=fresh,  # type: ignore[arg-type]
            reply_body=reply_body,
            missing_fields=[f.key for f in outcome.missing_fields],
            is_complete=outcome.is_complete,
            ticket_reference=ticket_reference,
        )

    # ---- helpers ----

    def _merge_fields(self, complaint, outcome, source_message_id: int) -> None:
        existing = {f.key: f for f in complaint.fields}
        schema = self.registry.try_get(complaint.type)
        specs = {s.key: s for s in schema.fields_for(complaint.method_key)} if schema else {}

        for extracted in outcome.newly_extracted:
            if extracted.key in existing and existing[extracted.key].value:
                continue  # never overwrite an already-collected field

            spec = specs.get(extracted.key)
            status = FieldStatus.EXTRACTED
            error = None
            value = extracted.value
            if spec is not None:
                result = validate_field(spec, extracted.value)
                if result.ok:
                    status = FieldStatus.VALIDATED
                    value = result.normalized_value or extracted.value
                else:
                    status = FieldStatus.INVALID
                    error = result.error

            row = existing.get(extracted.key)
            if row is None:
                row = ComplaintField(complaint_id=complaint.id, key=extracted.key)
                complaint.fields.append(row)
                existing[extracted.key] = row
            row.value = value
            row.status = status
            row.source = FieldSource.CUSTOMER_MESSAGE
            row.source_message_id = source_message_id
            row.confidence = extracted.confidence
            row.validation_error = error

    async def _send_reply(
        self, conversation: Conversation, to_addr: str, subject: str | None, body: str
    ) -> None:
        reply_subject = f"Re: {subject}" if subject else "Re: Your complaint"
        await self.email.send(
            OutboundEmail(
                to_addr=to_addr,
                from_addr=settings.support_inbox_address,
                subject=reply_subject,
                body=body,
                thread_id=str(conversation.id),
            )
        )
        self.conversations.add_message(
            conversation,
            direction=MessageDirection.OUTBOUND,
            sender=settings.support_inbox_address,
            recipient=to_addr,
            subject=reply_subject,
            body=body,
        )
        self._log_email(
            conversation, "outbound", settings.support_inbox_address, to_addr, reply_subject, body
        )

    def _log_email(
        self, conversation, direction, from_addr, to_addr, subject, body
    ) -> None:
        self.db.add(
            EmailLog(
                conversation_id=conversation.id,
                direction=direction,
                provider=self.email.name,
                from_addr=from_addr,
                to_addr=to_addr,
                subject=subject,
                body=body,
            )
        )
