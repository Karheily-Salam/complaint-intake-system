"""Records model outputs in ``ml_predictions``.

Called by IntakeService inside the turn's own transaction, so a prediction
row exists exactly when the turn it describes was committed - a rolled-back
turn (say, an SMTP failure) leaves no orphan prediction behind, and the retry
records its own.

Recording must never break intake: building a row is wrapped so a bug here
costs one missing log line, not a customer's email.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.base import Classification
from app.conversation.state import ExtractionAudit
from app.core.logging import get_logger
from app.db.models.ml_prediction import MLPrediction

logger = get_logger(__name__)


def record_classification(
    db: Session,
    *,
    conversation_id: int,
    message_id: int | None,
    is_demo: bool,
    language_code: str | None,
    classification: Classification,
    final_type: str | None,
    provider_name: str,
) -> None:
    try:
        ml = classification.ml
        db.add(
            MLPrediction(
                task="classification",
                model_name=ml.model_name if ml else f"provider:{provider_name}",
                model_version=ml.model_version if ml else "-",
                conversation_id=conversation_id,
                message_id=message_id,
                is_demo=is_demo,
                language_code=language_code,
                predicted_label=ml.label if ml else classification.type,
                confidence=ml.confidence if ml else classification.confidence,
                abstained=ml.abstained if ml else classification.type is None,
                final_label=final_type,
                decided_by=classification.decided_by,
                latency_ms=ml.latency_ms if ml else None,
                details={
                    "top_label": ml.top_label if ml else None,
                    "probabilities": ml.probabilities if ml else {},
                    "decision_confidence": round(classification.confidence, 4),
                },
            )
        )
    except Exception:
        logger.exception("Could not record classification prediction")


def record_extraction(
    db: Session,
    *,
    conversation_id: int,
    message_id: int | None,
    is_demo: bool,
    language_code: str | None,
    audit: ExtractionAudit,
    provider_name: str,
) -> None:
    """One row per extraction call: how many values were proposed, which were
    accepted on what evidence, and which were refused as unsupported."""
    try:
        db.add(
            MLPrediction(
                task="extraction",
                model_name=f"extractor:{provider_name}",
                model_version=audit.policy,
                conversation_id=conversation_id,
                message_id=message_id,
                is_demo=is_demo,
                language_code=language_code,
                predicted_label=None,
                confidence=None,
                abstained=audit.proposed == 0,
                final_label=None,
                decided_by="rules",
                latency_ms=audit.latency_ms,
                details={
                    "proposed": audit.proposed,
                    "accepted": dict(audit.accepted),
                    "rejected": list(audit.rejected),
                },
            )
        )
    except Exception:
        logger.exception("Could not record extraction audit")
