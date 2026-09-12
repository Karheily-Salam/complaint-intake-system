"""Running the evaluation sets through the real code paths.

Deliberately not a reimplementation: classification goes through the same
providers the service builds, and extraction goes through the conversation
engine, so schema validation and the evidence check are included in what is
measured. If the engine changes, the numbers change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.ai.base import TypeOption
from app.ai.providers.hybrid import HybridAIProvider
from app.ai.providers.rule_based import RuleBasedAIProvider
from app.conversation.engine import ConversationEngine
from app.conversation.state import ConversationState
from app.core.config import BACKEND_DIR
from app.domain.complaint_schemas.registry import get_registry
from app.ml.classifier import ABSTAIN_LABEL, LinearTextClassifier, get_classifier
from app.ml.evaluation import (
    ClassificationReport,
    ExtractionReport,
    build_classification_report,
    score_extraction,
)
from app.ml.training import Example, file_sha256, load_examples

DATASET_DIR = BACKEND_DIR / "datasets" / "complaints"
CLASSIFICATION_TEST = DATASET_DIR / "test.jsonl"
EXTRACTION_TEST = DATASET_DIR / "extraction_test.jsonl"
PROBLEM_DESCRIPTION = "problem_description"


@dataclass
class ExtractionCase:
    id: str
    language: str
    type: str
    pending_field: str | None
    reference_date: date | None
    text: str
    fields: dict[str, str]


def load_extraction_cases(path: Path = EXTRACTION_TEST) -> list[ExtractionCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append(
            ExtractionCase(
                id=row["id"],
                language=row["language"],
                type=row["type"],
                pending_field=row.get("pending_field"),
                reference_date=(
                    datetime.strptime(row["reference_date"], "%Y-%m-%d").date()
                    if row.get("reference_date")
                    else None
                ),
                text=row["text"],
                fields=row.get("fields", {}),
            )
        )
    return cases


def _options() -> list[TypeOption]:
    return [
        TypeOption(type=s.type, label=s.label, description=s.description)
        for s in get_registry().all()
    ]


async def evaluate_classification(
    system: str, examples: list[Example], classifier: LinearTextClassifier | None = None
) -> ClassificationReport:
    """``system`` is one of "rules", "ml" or "hybrid"."""
    options = _options()
    predictions: list[str] = []
    confidences: list[float] = []
    answered: list[bool] = []

    if system == "ml":
        model = classifier or get_classifier()
        if model is None:
            raise RuntimeError("no classifier artifact to evaluate")
        for example in examples:
            prediction = model.predict(example.text)
            predictions.append(prediction.label or ABSTAIN_LABEL)
            confidences.append(prediction.confidence)
            answered.append(not prediction.abstained)
        return build_classification_report(system, examples, predictions, confidences, answered)

    provider = RuleBasedAIProvider()
    if system == "hybrid":
        provider = HybridAIProvider(provider, classifier or get_classifier(), mode="assist")
    elif system != "rules":
        raise ValueError(f"unknown system '{system}'")

    for example in examples:
        result = await provider.classify(example.text, options)
        predictions.append(result.type or ABSTAIN_LABEL)
        confidences.append(result.confidence)
        answered.append(result.type is not None)
    return build_classification_report(system, examples, predictions, confidences, answered)


async def evaluate_extraction(
    cases: list[ExtractionCase], provider=None
) -> ExtractionReport:
    engine = ConversationEngine(provider or RuleBasedAIProvider(), get_registry())
    scored = []
    for case in cases:
        outcome = await engine.advance(
            ConversationState(
                latest_message=case.text,
                complaint_type=case.type,
                pending_field=case.pending_field,
                reference_date=case.reference_date,
            )
        )
        predicted = {
            f.key: f.value
            for f in outcome.fields
            # The free-text description is the customer's own words verbatim,
            # not a value to be right or wrong about, so it is not scored.
            if f.key != PROBLEM_DESCRIPTION and f.value
        }
        audit = outcome.extraction
        scored.append(
            {
                "id": case.id,
                "language": case.language,
                "gold": case.fields,
                "predicted": predicted,
                "evidence_methods": {
                    k: v for k, v in (audit.accepted if audit else {}).items()
                    if k != PROBLEM_DESCRIPTION
                },
                "rejected": [k for k in (audit.rejected if audit else [])],
            }
        )
    return score_extraction(scored)


async def full_report() -> dict:
    """Everything, with the inputs identified by hash so a number can be traced."""
    from app.ml.embeddings import get_embedder

    examples = load_examples(CLASSIFICATION_TEST)
    cases = load_extraction_cases()
    classifier = get_classifier()

    classification = {}
    for system in ("rules", "ml", "hybrid"):
        classification[system] = (
            await evaluate_classification(system, examples, classifier)
        ).as_dict()

    extraction = (await evaluate_extraction(cases)).as_dict()
    embedder = get_embedder()
    return {
        "datasets": {
            "classification_test": {
                "path": CLASSIFICATION_TEST.relative_to(BACKEND_DIR).as_posix(),
                "sha256": file_sha256(CLASSIFICATION_TEST),
                "n": len(examples),
            },
            "extraction_test": {
                "path": EXTRACTION_TEST.relative_to(BACKEND_DIR).as_posix(),
                "sha256": file_sha256(EXTRACTION_TEST),
                "n": len(cases),
            },
        },
        "models": {
            "classifier": {
                "name": classifier.metadata.get("model_name") if classifier else None,
                "version": classifier.version if classifier else None,
                "threshold": classifier.threshold if classifier else None,
                "temperature": round(classifier.temperature, 4) if classifier else None,
                "cross_validation": (
                    classifier.metadata.get("cross_validation") if classifier else {}
                ),
            },
            "embedder": {"name": embedder.name, "version": embedder.version, "dim": embedder.dim},
            "extractor": "rule_based + evidence check",
        },
        "classification": classification,
        "extraction": extraction,
    }
