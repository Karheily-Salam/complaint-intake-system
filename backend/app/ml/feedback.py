"""Reading staff corrections back as a learning signal.

The summary answers the questions a model owner asks before retraining: how
many corrections exist, of what kind, and which layer's decisions get
corrected most - a correction rate for the classifier's own decisions that
rises is the clearest sign the model has drifted from reality.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.ml_feedback import MLFeedback
from app.db.models.ml_prediction import MLPrediction


def _counts(db: Session, column, *filters) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).where(*filters).group_by(column)).all()
    return {str(value): count for value, count in rows if value is not None}


def feedback_summary(db: Session, *, include_demo: bool = False) -> dict:
    scope = [] if include_demo else [MLFeedback.is_demo.is_(False)]
    prediction_scope = [] if include_demo else [MLPrediction.is_demo.is_(False)]

    decided = _counts(
        db,
        MLPrediction.decided_by,
        MLPrediction.task == "classification",
        MLPrediction.final_label.is_not(None),
        *prediction_scope,
    )
    corrected = _counts(
        db, MLFeedback.original_source, MLFeedback.kind == "classification", *scope
    )
    rates = {
        layer: {
            "decisions": decided.get(layer, 0),
            "corrected": corrected.get(layer, 0),
            "correction_rate": (
                round(corrected.get(layer, 0) / decided[layer], 4) if decided.get(layer) else None
            ),
        }
        for layer in sorted(set(decided) | set(corrected))
    }
    return {
        "total": db.scalar(select(func.count()).select_from(MLFeedback).where(*scope)) or 0,
        "not_exported": db.scalar(
            select(func.count())
            .select_from(MLFeedback)
            .where(MLFeedback.exported.is_(False), *scope)
        )
        or 0,
        "by_kind": _counts(db, MLFeedback.kind, *scope),
        "by_field": _counts(db, MLFeedback.field_key, MLFeedback.kind == "field", *scope),
        "by_model_version": _counts(db, MLFeedback.model_version, *scope),
        "classification_by_layer": rates,
        "include_demo": include_demo,
    }


def recent_feedback(
    db: Session, *, limit: int = 50, include_demo: bool = False
) -> list[MLFeedback]:
    stmt = select(MLFeedback).order_by(MLFeedback.id.desc()).limit(limit)
    if not include_demo:
        stmt = stmt.where(MLFeedback.is_demo.is_(False))
    return list(db.scalars(stmt))
