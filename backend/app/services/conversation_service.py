"""Read-side helpers for conversations (used by the API and, later, the dashboard)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.conversation import Conversation
from app.repositories.conversation_repo import ConversationRepository


class ConversationService:
    def __init__(self, db: Session) -> None:
        self.conversations = ConversationRepository(db)

    def get(self, conversation_id: int) -> Conversation | None:
        return self.conversations.get(conversation_id)

    def list_recent(self, limit: int = 50) -> list[Conversation]:
        return self.conversations.list_recent(limit=limit)
