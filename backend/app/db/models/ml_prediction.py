from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base_class import Base, TimestampMixin


class MLPrediction(Base, TimestampMixin):
    """One model output, recorded next to what the system actually decided.

    The audit trail for everything statistical in the pipeline: which model
    and version produced what, how confident it was, whether it abstained,
    how long it took, and what the deterministic workflow did with it. It is
    what evaluation on real traffic, drift monitoring and staff-correction
    feedback are computed from.

    Never stores message text or extracted values - labels, keys, scores and
    timings only - so this table is not a second copy of customer data.
    """

    __tablename__ = "ml_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # classification | extraction | language
    task: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True, index=True
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    # Mirrors Conversation.is_demo so monitoring can report on real traffic
    # without joining - demo input is unverified and anyone can send it.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # What the model said. For classification: its top class (None when it
    # abstained), for language: the detected code.
    predicted_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    abstained: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # What the deterministic workflow used, and which layer it came from
    # (rules | ml | provider). Differs from predicted_label whenever the rules
    # overruled the model or the model abstained.
    final_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
