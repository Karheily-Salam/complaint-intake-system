"""Internal operations view - staff only.

Answers the question a small production service actually gets asked: is intake
still working, and what has it done? A poller that has quietly stopped is
indistinguishable from a quiet mailbox unless something reports on it.

Deliberately counts and timestamps only. No customer identifiers, no message
content, no configuration values, and no credentials - so nothing here becomes
a second way to read customer data.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DbDep
from app.api.security import StaffAuth
from app.core.config import settings
from app.db.models.conversation import Conversation
from app.db.models.ticket import Ticket
from app.services.email_poller import poller_health

router = APIRouter(dependencies=[StaffAuth])


def _counts_by(db, column) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    return {str(value): count for value, count in rows}


@router.get("/stats")
def operational_stats(db: DbDep) -> dict:
    tickets_total = db.scalar(select(func.count()).select_from(Ticket)) or 0
    conversations_total = db.scalar(select(func.count()).select_from(Conversation)) or 0
    demo_conversations = (
        db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.is_demo.is_(True))
        )
        or 0
    )

    return {
        "email": {
            # Which transport is live matters more than any other single fact
            # here: "mock" means no customer mail is being received at all.
            "provider": settings.email_provider,
            "provider_configured": not settings.missing_email_settings(),
            "poll_interval_seconds": settings.email_poll_interval_seconds,
            "poller": poller_health.snapshot(),
        },
        "tickets": {
            "total": tickets_total,
            "by_status": _counts_by(db, Ticket.status),
            "by_type": _counts_by(db, Ticket.type),
        },
        "conversations": {
            "total": conversations_total,
            "demo": demo_conversations,
            "real": conversations_total - demo_conversations,
            "by_status": _counts_by(db, Conversation.status),
        },
    }
