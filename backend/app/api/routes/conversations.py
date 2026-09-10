from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import DbDep
from app.schemas.conversation import ConversationOut
from app.services.conversation_service import ConversationService

router = APIRouter()


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    db: DbDep,
    # Bounded for the same reason as the ticket listing: this is a public
    # endpoint and each conversation drags its complaint with it.
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ConversationOut]:
    return ConversationService(db).list_recent(limit=limit)  # type: ignore[return-value]


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: int, db: DbDep) -> ConversationOut:
    convo = ConversationService(db).get(conversation_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo  # type: ignore[return-value]
