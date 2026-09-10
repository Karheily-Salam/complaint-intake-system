from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.complaint import Complaint
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.domain.enums import MessageDirection


class ConversationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, conversation_id: int) -> Conversation | None:
        return self.db.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(
                selectinload(Conversation.messages),
                selectinload(Conversation.complaint).selectinload(Complaint.fields),
            )
        )

    def list_recent(self, limit: int = 50) -> list[Conversation]:
        return list(
            self.db.scalars(
                select(Conversation)
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
                .options(selectinload(Conversation.complaint))
            )
        )

    def create(self, customer_id: int, subject: str | None) -> Conversation:
        convo = Conversation(
            customer_id=customer_id,
            subject=subject,
            thread_token=uuid.uuid4().hex[:16],
        )
        self.db.add(convo)
        self.db.flush()
        return convo

    def get_by_thread_token(self, thread_token: str) -> Conversation | None:
        return self.db.scalar(
            select(Conversation)
            .where(Conversation.thread_token == thread_token)
            .options(
                selectinload(Conversation.messages),
                selectinload(Conversation.complaint).selectinload(Complaint.fields),
            )
        )

    def find_by_external_message_id(self, external_message_id: str) -> Message | None:
        """Look up the message we recorded for a given real email Message-ID."""
        return self.db.scalar(
            select(Message).where(Message.external_message_id == external_message_id)
        )

    def add_message(
        self,
        conversation: Conversation,
        *,
        direction: MessageDirection,
        sender: str,
        body: str,
        recipient: str | None = None,
        subject: str | None = None,
        raw_meta: dict | None = None,
        external_message_id: str | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation.id,
            direction=direction,
            sender=sender,
            recipient=recipient,
            subject=subject,
            body=body,
            raw_meta=raw_meta or {},
            external_message_id=external_message_id,
        )
        self.db.add(message)
        self.db.flush()
        return message

    def get_or_create_complaint(self, conversation: Conversation) -> Complaint:
        if conversation.complaint is None:
            conversation.complaint = Complaint(conversation_id=conversation.id)
            self.db.add(conversation.complaint)
            self.db.flush()
        return conversation.complaint

    def inbound_bodies(self, conversation: Conversation) -> list[str]:
        return [
            m.body for m in conversation.messages if m.direction == MessageDirection.INBOUND
        ]
