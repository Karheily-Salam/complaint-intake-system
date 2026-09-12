"""Similar and possibly-duplicate tickets, for support staff to look at.

Semantic similarity alone makes a poor duplicate detector: in a complaint
inbox, *every* withdrawal complaint sounds like every other one. So the
decision combines three signals, and the text is only one of them:

1. **Semantic similarity** of the two complaints' embeddings
   (:mod:`app.ml.embeddings`), against the active model's calibrated
   thresholds.
2. **Customer identity** - the same customer record, which is the sender
   address (the system never infers identity from anything in the body).
3. **Extracted fields** with the same value - the same transaction id, the
   same user id, the same transaction date.

A ticket is flagged ``possible_duplicate`` only when the evidence is strong:

* the same customer, the same complaint type, and either near-identical text
  or a matching identifying field; or
* the same transaction reference on a complaint of the same type, whoever
  sent it - two tickets about one transaction are one problem.

Everything else that is merely close in meaning is ``similar``. Each result
carries the reasons that produced it, so an agent can see why.

This only ever *suggests*. Nothing here merges, links, closes or changes a
ticket - acting on a suggestion is a staff decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models.complaint import Complaint
from app.db.models.complaint_field import ComplaintField
from app.db.models.conversation import Conversation
from app.db.models.ticket import Ticket
from app.ml.embedding_store import embed_pending, ensure_vector, load_vectors
from app.ml.embeddings import Embedder, get_embedder

POSSIBLE_DUPLICATE = "possible_duplicate"
SIMILAR = "similar"

# Fields whose equal values identify the same underlying transaction.
_STRONG_FIELD_MARKERS = ("transaction_id", "txn", "transaction_hash", "reference")
# Fields that corroborate, but alone do not prove, the same matter.
_SUPPORTING_FIELDS = ("user_id", "account_email", "transaction_date", "source_wallet_or_account")
_MAX_CANDIDATES = 50


@dataclass
class SimilarTicket:
    reference: str
    type: str
    status: str
    created_at: datetime
    similarity: float
    relation: str
    same_customer: bool
    matched_fields: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class SimilarityResult:
    model_version: str
    similar_threshold: float
    duplicate_threshold: float
    items: list[SimilarTicket]


def _is_strong(key: str) -> bool:
    return any(marker in key for marker in _STRONG_FIELD_MARKERS)


def _field_values(complaint: Complaint) -> dict[str, str]:
    return {
        f.key: " ".join(f.value.lower().split())
        for f in complaint.fields
        if f.value and (_is_strong(f.key) or f.key in _SUPPORTING_FIELDS)
    }


def find_similar(
    db: Session, ticket: Ticket, *, limit: int = 5, embedder: Embedder | None = None
) -> SimilarityResult:
    embedder = embedder or get_embedder()
    complaint = ticket.complaint
    is_demo = ticket.conversation.is_demo
    own_fields = _field_values(complaint)

    # Catch up on anything the background worker has not embedded yet, so a
    # ticket that arrived seconds ago can already be found. Bounded, and a
    # no-op when the worker is keeping up.
    embed_pending(db, embedder, limit=64)
    vector = ensure_vector(db, complaint, embedder)
    scores: dict[int, float] = {}
    if vector is not None:
        vectors = load_vectors(
            db, is_demo=is_demo, embedder=embedder, exclude_complaint_id=complaint.id
        )
        if vectors.complaint_ids:
            sims = vectors.matrix @ vector
            order = np.argsort(-sims)[:_MAX_CANDIDATES]
            scores = {vectors.complaint_ids[i]: float(sims[i]) for i in order}

    # Identity- and field-based candidates are considered even when the text
    # is not close: "same transaction id" matters however it was described.
    candidate_ids = set(scores)
    candidate_ids |= set(
        db.scalars(
            select(Ticket.complaint_id).where(
                Ticket.customer_id == ticket.customer_id, Ticket.id != ticket.id
            )
        )
    )
    rows = db.scalars(
        select(Ticket)
        .where(Ticket.complaint_id.in_(candidate_ids), Ticket.id != ticket.id)
        .options(
            selectinload(Ticket.complaint).selectinload(Complaint.fields),
            selectinload(Ticket.conversation),
        )
    ).all()
    # Strong-field matches across customers need a value lookup rather than a
    # vector: find tickets whose strong fields share a value with ours.
    strong_values = {k: v for k, v in own_fields.items() if _is_strong(k)}
    if strong_values:
        rows = list(rows) + [
            t for t in _tickets_sharing_values(db, strong_values, ticket, is_demo)
            if t.complaint_id not in candidate_ids
        ]

    items: list[SimilarTicket] = []
    for other in rows:
        if other.conversation.is_demo != is_demo:
            continue  # demo and real data never meet
        similarity = scores.get(other.complaint_id, 0.0)
        same_customer = other.customer_id == ticket.customer_id
        same_type = other.type == ticket.type
        other_fields = _field_values(other.complaint)
        matched = sorted(k for k, v in own_fields.items() if other_fields.get(k) == v)
        strong_match = any(_is_strong(k) for k in matched)

        reasons: list[str] = []
        if similarity >= embedder.similar_threshold:
            reasons.append("semantic_similarity")
        if same_customer:
            reasons.append("same_customer")
        if same_type:
            reasons.append("same_type")
        reasons += [f"same_{k}" for k in matched]

        duplicate = same_type and (
            strong_match
            or (
                same_customer
                and (similarity >= embedder.duplicate_threshold or len(matched) >= 2)
            )
        )
        if duplicate:
            relation = POSSIBLE_DUPLICATE
        elif similarity >= embedder.similar_threshold or (same_customer and matched):
            relation = SIMILAR
        else:
            continue

        items.append(
            SimilarTicket(
                reference=other.reference,
                type=other.type,
                status=other.status,
                created_at=other.created_at,
                similarity=round(similarity, 4),
                relation=relation,
                same_customer=same_customer,
                matched_fields=matched,
                reasons=reasons,
            )
        )

    items.sort(key=lambda s: (s.relation != POSSIBLE_DUPLICATE, -s.similarity))
    return SimilarityResult(
        model_version=embedder.version,
        similar_threshold=embedder.similar_threshold,
        duplicate_threshold=embedder.duplicate_threshold,
        items=items[:limit],
    )


def _tickets_sharing_values(
    db: Session, values: dict[str, str], ticket: Ticket, is_demo: bool
) -> list[Ticket]:
    matches: list[Ticket] = []
    for key, value in values.items():
        stmt = (
            select(Ticket)
            .join(ComplaintField, ComplaintField.complaint_id == Ticket.complaint_id)
            .join(Conversation, Conversation.id == Ticket.conversation_id)
            .where(
                ComplaintField.key == key,
                func.lower(func.trim(ComplaintField.value)) == value,
                Ticket.id != ticket.id,
                Conversation.is_demo.is_(is_demo),
            )
            .options(
                selectinload(Ticket.complaint).selectinload(Complaint.fields),
                selectinload(Ticket.conversation),
            )
        )
        for candidate in db.scalars(stmt):
            field_value = next(
                (f.value for f in candidate.complaint.fields if f.key == key), None
            )
            if field_value and " ".join(field_value.lower().split()) == value:
                matches.append(candidate)
    unique = {t.id: t for t in matches}
    return list(unique.values())
