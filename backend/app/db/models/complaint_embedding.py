from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin


class ComplaintEmbedding(Base, TimestampMixin):
    """One complaint's text embedding under one model version.

    Stored in the application database, not a vector database: at this
    system's scale (thousands of complaints) an exact cosine scan over a numpy
    matrix takes milliseconds, and a separate service would be one more thing
    to run, back up and secure on a 1-vCPU host.

    The vector is the float32 bytes of an L2-normalised array. ``text_sha256``
    is a hash of the masked text that was embedded, so a complaint whose
    description changes is re-embedded, while an unchanged one never is.
    """

    __tablename__ = "complaint_embeddings"
    __table_args__ = (
        UniqueConstraint("complaint_id", "model_version", name="uq_complaint_embedding_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), index=True, nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
