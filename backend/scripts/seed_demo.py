"""Populate (or reset) the demo dataset.

A recruiter opening the deployed app should find a system with something in
it, not an empty dashboard. This drives the *real* intake path - the same
engine, validation and ticket creation a customer email goes through - so the
seeded data is genuine output rather than fabricated rows that could drift
from how the system actually behaves.

    python -m scripts.seed_demo            # add the demo conversations
    python -m scripts.seed_demo --reset    # remove demo data first, then add

Safety: every record created here is demo-scoped (``is_demo``), and --reset
deletes *only* demo-scoped rows. Real customer data is never touched, and the
deletion is filtered in the query rather than by convention.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.db.base import Base  # noqa: F401 - ensures every mapper is registered
from app.db.models.complaint import Complaint
from app.db.models.complaint_embedding import ComplaintEmbedding
from app.db.models.complaint_field import ComplaintField
from app.db.models.conversation import Conversation
from app.db.models.customer import Customer
from app.db.models.email_log import EmailLog
from app.db.models.message import Message
from app.db.models.ml_feedback import MLFeedback
from app.db.models.ml_prediction import MLPrediction
from app.db.models.ticket import Ticket
from app.schemas.conversation import InboundEmailIn
from app.services.intake_service import IntakeService

# Each entry is one conversation: a sequence of customer emails. The system's
# replies are produced by the engine, never written here.
SCENARIOS: list[dict] = [
    {
        "name": "Amelia Hart",
        "email": "amelia.hart@example.com",
        "subject": "Problem with my account",
        "messages": [
            "Hello, I have a problem with my account and I am not happy.",
            "It is about a withdrawal I requested that never arrived.",
            "U-482913",
            "amelia.hart@example.com",
            "TXN-4c81de92",
        ],
    },
    {
        "name": "Noah Reed",
        "email": "noah.reed@example.com",
        "subject": "Withdrawal has not arrived",
        "messages": [
            "My withdrawal has not arrived. My user id is U-771204, my account email "
            "is noah.reed@example.com and the transaction id is TXN-9f3a12bc.",
        ],
    },
    {
        "name": "Sofia Marino",
        "email": "sofia.marino@example.com",
        "subject": "Cannot withdraw my funds",
        "messages": [
            "I cannot withdraw my money, it has been stuck for two days.",
            "U-660145",
            "Account email: sofia.marino@example (I think that's right)",
            "Sorry, it is sofia.marino@example.com",
            "TXN-77aa0011",
        ],
    },
    {
        "name": "Liam Okafor",
        "email": "liam.okafor@example.com",
        "subject": "Deposit missing from my balance",
        "messages": [
            "My deposit never showed up in my balance.",
            "U-330871",
            "liam.okafor@example.com",
            "Sberbank wallet",
            "2026-09-08",
            "I paid by bank transfer and the money left my account but never arrived.",
        ],
    },
    {
        # Shows that the reply language follows the customer's own.
        "name": "Ekaterina Volkova",
        "email": "ekaterina.volkova@example.com",
        "subject": "Проблема с выводом средств",
        "messages": [
            "Здравствуйте, у меня проблема с выводом денег. Деньги не поступили.",
            "U-905512",
            "ekaterina.volkova@example.com",
            "TXN-5501ab77",
        ],
    },
]


def reset_demo_data() -> int:
    """Delete demo conversations and demo customers. Never real records.

    Deletion is explicit and ordered rather than left to ORM cascades:
    complaint_fields reference both complaints *and* messages, and email_logs
    reference conversations without a relationship, so cascade ordering is not
    guaranteed to satisfy the foreign keys. (With `PRAGMA foreign_keys=ON`
    SQLite enforces this properly, which is how the ordering bug surfaced.)

    Every statement below is scoped to ids already filtered by ``is_demo``, so
    a real conversation cannot be caught up in it.
    """
    with SessionLocal() as db:
        demo_ids = list(
            db.scalars(select(Conversation.id).where(Conversation.is_demo.is_(True)))
        )
        if not demo_ids:
            return 0

        complaint_ids = list(
            db.scalars(select(Complaint.id).where(Complaint.conversation_id.in_(demo_ids)))
        )

        # Children first, parents last.
        db.execute(delete(MLFeedback).where(MLFeedback.conversation_id.in_(demo_ids)))
        db.execute(delete(Ticket).where(Ticket.conversation_id.in_(demo_ids)))
        if complaint_ids:
            db.execute(
                delete(ComplaintField).where(ComplaintField.complaint_id.in_(complaint_ids))
            )
            db.execute(
                delete(ComplaintEmbedding).where(
                    ComplaintEmbedding.complaint_id.in_(complaint_ids)
                )
            )
            db.execute(delete(Complaint).where(Complaint.id.in_(complaint_ids)))
        db.execute(delete(EmailLog).where(EmailLog.conversation_id.in_(demo_ids)))
        db.execute(delete(MLPrediction).where(MLPrediction.conversation_id.in_(demo_ids)))
        db.execute(delete(Message).where(Message.conversation_id.in_(demo_ids)))
        db.execute(delete(Conversation).where(Conversation.id.in_(demo_ids)))
        db.execute(delete(Customer).where(Customer.is_demo.is_(True)))

        db.commit()
    return len(demo_ids)


async def seed() -> list[tuple[str, str | None]]:
    created: list[tuple[str, str | None]] = []
    for scenario in SCENARIOS:
        conversation_id: int | None = None
        reference: str | None = None
        for body in scenario["messages"]:
            with SessionLocal() as db:
                result = await IntakeService(db).handle_inbound(
                    InboundEmailIn(
                        from_addr=scenario["email"],
                        customer_name=scenario["name"],
                        subject=scenario["subject"],
                        body=body,
                        conversation_id=conversation_id,
                    )
                )
                conversation_id = result.conversation.id
                reference = result.ticket_reference or reference
        created.append((scenario["name"], reference))
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete existing demo data first (real data is never touched)",
    )
    args = parser.parse_args()

    if args.reset:
        removed = reset_demo_data()
        print(f"Removed {removed} demo conversation(s).")

    created = asyncio.run(seed())
    print(f"\nSeeded {len(created)} demo conversations:")
    for name, reference in created:
        print(f"  {name:24} ticket {reference or '(still collecting information)'}")
    print("\nOpen the app and check the Support inbox tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
