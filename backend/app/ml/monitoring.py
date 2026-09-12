"""Monitoring: what the models are doing in production, right now.

Offline evaluation (:mod:`app.ml.evaluation`) says how good a model is on a
fixed test set. This says what is happening on live traffic, which is the
part that changes without anyone editing code:

* **Volume and versions** - how many predictions, from which model version.
  If two versions appear, a deploy is in progress or a rollback is needed.
* **Where decisions come from** - keyword rules, the classifier, or the
  provider - and how often the classifier abstains. A rising abstention rate
  means the incoming mail no longer looks like the training data.
* **Shadow agreement** - on every message the rules decided, the classifier's
  own opinion was recorded anyway, so the two can be compared continuously on
  real traffic without any risk to a customer.
* **Latency** - the classifier sits between a customer's email and the reply,
  which is held to about a second end to end.
* **Drift** - PSI between the recent window and the one before it, for the
  predicted-label mix and for the confidence distribution.
* **Correction rate** - how often staff had to fix what a layer decided. The
  only ground truth this system gets from production.

Counts, rates and versions only. No message text, no addresses, no values.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.ml_prediction import MLPrediction
from app.domain.evidence import EVIDENCE_POLICY_VERSION
from app.ml.classifier import get_classifier
from app.ml.embeddings import get_embedder
from app.ml.evaluation import percentile, population_stability_index
from app.ml.feedback import feedback_summary
from app.services.ml_worker import ml_worker_health

_MAX_ROWS = 5000
_CONFIDENCE_BUCKETS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01))


def model_registry() -> dict:
    """Every model and policy version the running service is using."""
    classifier = get_classifier()
    embedder = get_embedder()
    configured = settings.embedding_provider.lower()
    return {
        "classifier": {
            "name": classifier.metadata.get("model_name") if classifier else None,
            "version": classifier.version if classifier else None,
            "mode": settings.ml_classifier_mode,
            "threshold": classifier.threshold if classifier else None,
            "temperature": round(classifier.temperature, 4) if classifier else None,
            "labels": classifier.labels if classifier else [],
            "trained_on": (classifier.metadata.get("training") or {}).get("examples")
            if classifier
            else None,
            "cross_validation": (classifier.metadata.get("cross_validation") or {})
            if classifier
            else {},
            "available": classifier is not None,
        },
        "embedder": {
            "name": embedder.name,
            "version": embedder.version,
            "dim": embedder.dim,
            "configured": configured,
            # True when ONNX was asked for but the hashing fallback is running.
            "fallback_active": (
                configured == "onnx" and embedder.name != "multilingual-e5-small-int8"
            ),
            "similar_threshold": embedder.similar_threshold,
            "duplicate_threshold": embedder.duplicate_threshold,
        },
        "extraction": {
            "evidence_policy": EVIDENCE_POLICY_VERSION,
            "require_evidence": settings.extraction_require_evidence,
        },
        "ai_provider": settings.ai_provider,
    }


def _bucket(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    for low, high in _CONFIDENCE_BUCKETS:
        if low <= confidence < high:
            return f"{low:g}-{high if high <= 1 else 1:g}"
    return "unknown"


def _rows(db: Session, since: datetime, until: datetime | None, include_demo: bool):
    stmt = select(MLPrediction).where(MLPrediction.created_at >= since)
    if until is not None:
        stmt = stmt.where(MLPrediction.created_at < until)
    if not include_demo:
        stmt = stmt.where(MLPrediction.is_demo.is_(False))
    return list(db.scalars(stmt.order_by(MLPrediction.id.desc()).limit(_MAX_ROWS)))


def monitoring_snapshot(db: Session, *, days: int = 7, include_demo: bool = False) -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    recent_since = now - timedelta(days=days)
    reference_since = recent_since - timedelta(days=days)

    recent = _rows(db, recent_since, None, include_demo)
    reference = _rows(db, reference_since, recent_since, include_demo)

    classification = [r for r in recent if r.task == "classification"]
    extraction = [r for r in recent if r.task == "extraction"]
    confidences = [r.confidence for r in classification if r.confidence is not None]

    agreed = compared = 0
    for row in classification:
        model_label = (row.details or {}).get("top_label")
        if not model_label or row.decided_by == "ml" or not row.final_label:
            continue
        compared += 1
        agreed += int(model_label == row.final_label)

    proposed = sum((r.details or {}).get("proposed", 0) for r in extraction)
    rejected = sum(len((r.details or {}).get("rejected", [])) for r in extraction)
    methods: Counter[str] = Counter()
    for row in extraction:
        methods.update((row.details or {}).get("accepted", {}).values())

    return {
        "window_days": days,
        "include_demo": include_demo,
        "since": recent_since.isoformat(),
        "models": model_registry(),
        "predictions": {
            "total": len(recent),
            "by_task": dict(Counter(r.task for r in recent)),
            "by_model_version": dict(Counter(r.model_version for r in recent)),
        },
        "classification": {
            "n": len(classification),
            "by_decided_by": dict(Counter(r.decided_by or "unknown" for r in classification)),
            "abstention_rate": (
                round(sum(1 for r in classification if r.abstained) / len(classification), 4)
                if classification
                else None
            ),
            "label_distribution": dict(
                Counter(r.final_label or "unclear" for r in classification)
            ),
            "by_language": dict(Counter(r.language_code or "unknown" for r in classification)),
            "confidence": {
                "mean": round(sum(confidences) / len(confidences), 4) if confidences else None,
                "p50": percentile(confidences, 50),
                "p90": percentile(confidences, 90),
                "buckets": dict(Counter(_bucket(c) for c in confidences)),
            },
            "latency_ms": {
                "p50": percentile([r.latency_ms for r in classification if r.latency_ms], 50),
                "p95": percentile([r.latency_ms for r in classification if r.latency_ms], 95),
            },
            # The always-on shadow check: what the model would have said on the
            # messages a deterministic rule decided.
            "shadow_agreement": {
                "compared": compared,
                "agreed": agreed,
                "rate": round(agreed / compared, 4) if compared else None,
            },
        },
        "extraction": {
            "n": len(extraction),
            "values_proposed": proposed,
            "values_rejected": rejected,
            "rejection_rate": round(rejected / proposed, 4) if proposed else None,
            "evidence_methods": dict(sorted(methods.items())),
            "latency_ms": {
                "p50": percentile([r.latency_ms for r in extraction if r.latency_ms], 50),
                "p95": percentile([r.latency_ms for r in extraction if r.latency_ms], 95),
            },
        },
        "drift": _drift(recent, reference),
        "feedback": feedback_summary(db, include_demo=include_demo),
        "worker": ml_worker_health.snapshot(),
    }


def _drift(recent: list[MLPrediction], reference: list[MLPrediction]) -> dict:
    def labels(rows):
        return Counter(r.final_label or "unclear" for r in rows if r.task == "classification")

    def buckets(rows):
        return Counter(
            _bucket(r.confidence) for r in rows if r.task == "classification" and r.confidence
        )

    label_psi = population_stability_index(labels(reference), labels(recent))
    confidence_psi = population_stability_index(buckets(reference), buckets(recent))
    comparable = bool(labels(reference)) and bool(labels(recent))
    return {
        "reference_n": sum(labels(reference).values()),
        "recent_n": sum(labels(recent).values()),
        "label_psi": label_psi if comparable else None,
        "confidence_psi": confidence_psi if comparable else None,
        # Standard PSI reading; None when there is nothing to compare yet.
        "status": (
            _psi_status(max(label_psi, confidence_psi)) if comparable else "insufficient_data"
        ),
    }


def _psi_status(psi: float) -> str:
    if psi < 0.1:
        return "stable"
    return "moderate_shift" if psi < 0.25 else "significant_shift"
