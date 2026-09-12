"""Computing, storing and loading complaint embeddings.

The text embedded for a complaint is its concise description (or, before one
exists, the customer's messages with quoted history removed), with
identifiers masked (:func:`app.ml.pii.mask_identifiers`). Masking matters
twice over: an account number must not make two unrelated complaints look
alike, and the derived vector should carry as little personal data as
possible.

All reads are scoped by the demo flag, like every other query in the
system: demo complaints are compared only with demo complaints and real ones
only with real ones.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models.complaint import Complaint
from app.db.models.complaint_embedding import ComplaintEmbedding
from app.db.models.conversation import Conversation
from app.domain.enums import MessageDirection
from app.email.quoting import strip_quoted_reply
from app.ml.embeddings import Embedder, get_embedder
from app.ml.pii import mask_identifiers

_MAX_TEXT_CHARS = 1500


def complaint_text(complaint: Complaint) -> str:
    """The masked text that represents a complaint for similarity purposes."""
    text = complaint.concise_description or ""
    if not text.strip():
        conversation = complaint.conversation
        inbound = [
            strip_quoted_reply(m.body)
            for m in (conversation.messages if conversation else [])
            if m.direction == MessageDirection.INBOUND
        ]
        text = " ".join(inbound)
    return mask_identifiers(" ".join(text.split()))[:_MAX_TEXT_CHARS]


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _to_bytes(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_bytes(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim)


def embed_pending(db: Session, embedder: Embedder | None = None, *, limit: int = 64) -> int:
    """Embed complaints that have no vector for the active model, or a stale one.

    Stale means the complaint row changed after its vector was written; the
    text hash then decides whether anything actually needs recomputing.
    Returns how many vectors were written. The caller commits.
    """
    embedder = embedder or get_embedder()
    stmt = (
        select(Complaint, ComplaintEmbedding)
        .outerjoin(
            ComplaintEmbedding,
            and_(
                ComplaintEmbedding.complaint_id == Complaint.id,
                ComplaintEmbedding.model_version == embedder.version,
            ),
        )
        .where(
            or_(
                ComplaintEmbedding.id.is_(None),
                Complaint.updated_at > ComplaintEmbedding.updated_at,
            )
        )
        .order_by(Complaint.id.desc())
        .limit(limit)
        .options(selectinload(Complaint.conversation).selectinload(Conversation.messages))
    )
    todo: list[tuple[Complaint, ComplaintEmbedding | None, str]] = []
    for complaint, existing in db.execute(stmt).all():
        text = complaint_text(complaint)
        if not text.strip():
            continue
        if existing is not None and existing.text_sha256 == _text_hash(text):
            # Unchanged text: just mark the vector current (database clock,
            # like every other timestamp here) so it stops looking stale.
            existing.updated_at = func.now()
            continue
        todo.append((complaint, existing, text))
    if not todo:
        db.flush()
        return 0

    vectors = embedder.embed([t for _, _, t in todo])
    for (complaint, existing, text), vector in zip(todo, vectors, strict=True):
        row = existing or ComplaintEmbedding(
            complaint_id=complaint.id,
            model_name=embedder.name,
            model_version=embedder.version,
            dim=embedder.dim,
        )
        row.vector = _to_bytes(vector)
        row.text_sha256 = _text_hash(text)
        row.updated_at = func.now()
        db.add(row)
    db.flush()
    return len(todo)


def ensure_vector(db: Session, complaint: Complaint, embedder: Embedder | None = None):
    """This complaint's vector, computing and storing it now if needed."""
    embedder = embedder or get_embedder()
    text = complaint_text(complaint)
    if not text.strip():
        return None
    row = db.scalar(
        select(ComplaintEmbedding).where(
            ComplaintEmbedding.complaint_id == complaint.id,
            ComplaintEmbedding.model_version == embedder.version,
        )
    )
    digest = _text_hash(text)
    if row is not None and row.text_sha256 == digest:
        return from_bytes(row.vector, row.dim)
    vector = embedder.embed([text])[0]
    if row is None:
        row = ComplaintEmbedding(
            complaint_id=complaint.id,
            model_name=embedder.name,
            model_version=embedder.version,
            dim=embedder.dim,
        )
        db.add(row)
    row.vector = _to_bytes(vector)
    row.text_sha256 = digest
    db.flush()
    return vector


@dataclass
class VectorSet:
    complaint_ids: list[int]
    created_at: list[datetime]
    matrix: np.ndarray  # (n, dim)


def load_vectors(
    db: Session,
    *,
    is_demo: bool,
    embedder: Embedder | None = None,
    since: datetime | None = None,
    exclude_complaint_id: int | None = None,
) -> VectorSet:
    """Every stored vector of the active model in one demo/real scope."""
    embedder = embedder or get_embedder()
    stmt = (
        select(ComplaintEmbedding.complaint_id, Complaint.created_at, ComplaintEmbedding.vector)
        .join(Complaint, Complaint.id == ComplaintEmbedding.complaint_id)
        .join(Conversation, Conversation.id == Complaint.conversation_id)
        .where(
            ComplaintEmbedding.model_version == embedder.version,
            Conversation.is_demo.is_(is_demo),
        )
    )
    if since is not None:
        stmt = stmt.where(Complaint.created_at >= since)
    if exclude_complaint_id is not None:
        stmt = stmt.where(ComplaintEmbedding.complaint_id != exclude_complaint_id)
    rows = db.execute(stmt.order_by(Complaint.created_at)).all()
    if not rows:
        return VectorSet([], [], np.zeros((0, embedder.dim), dtype=np.float32))
    return VectorSet(
        complaint_ids=[r[0] for r in rows],
        created_at=[r[1] for r in rows],
        matrix=np.vstack([from_bytes(r[2], embedder.dim) for r in rows]),
    )
