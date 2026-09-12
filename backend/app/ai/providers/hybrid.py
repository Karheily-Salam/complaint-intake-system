"""Deterministic rules first, a calibrated classifier second.

Wraps any :class:`AIProvider` and changes only ``classify``; extraction,
summaries, language detection and reply writing are delegated untouched.

Decision order for the complaint type (``assist`` mode):

1. **Keyword rules.** An explicit withdrawal/deposit keyword is a
   deterministic, auditable match and always wins - the model is never
   allowed to overrule it.
2. **The classifier**, only when the rules found no keyword, and only when it
   is confident: its calibrated probability must clear the threshold chosen
   from the risk-coverage curve, and its top class must not be "unclear".
3. **The wrapped provider's own classify** otherwise - for the rule-based
   provider that is its "other"-or-ask-the-customer fallback, i.e. exactly
   the pre-ML behaviour.

The engine then applies its own confidence gate
(``MIN_CLASSIFICATION_CONFIDENCE``) as before, so a prediction still has to
pass the deterministic workflow to take effect.

``shadow`` mode computes and records the model's opinion but always returns
the wrapped provider's answer, which is how a model can be measured on real
traffic before it is trusted with any decision.
"""

from __future__ import annotations

from app.ai.base import (
    AIProvider,
    Classification,
    ExtractionResult,
    LanguageDetection,
    MLSignal,
    ReplyDraft,
    ReplyRequest,
    TypeOption,
)
from app.ai.providers.rule_based import keyword_classification
from app.core.logging import get_logger
from app.domain.complaint_schemas.spec import FieldSpec
from app.ml.classifier import ClassifierPrediction, LinearTextClassifier

logger = get_logger(__name__)

MODES = ("off", "shadow", "assist")


class HybridAIProvider(AIProvider):
    def __init__(
        self,
        base: AIProvider,
        classifier: LinearTextClassifier | None,
        *,
        mode: str = "assist",
        threshold: float | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown ML classifier mode '{mode}'. Expected one of {MODES}.")
        self.base = base
        self.classifier = classifier
        self.mode = mode
        self.threshold = threshold
        self.name = base.name

    async def available(self) -> bool:
        return await self.base.available()

    async def classify(self, message: str, options: list[TypeOption]) -> Classification:
        signal = self._predict(message)
        allowed = {o.type for o in options}

        if self.mode == "shadow":
            return _with_signal(await self.base.classify(message, options), signal)

        keyword = keyword_classification(message, options)
        if keyword is not None:
            return _with_signal(keyword, signal)

        if signal is not None and signal.label is not None and signal.label in allowed:
            return Classification(
                type=signal.label,
                confidence=signal.confidence,
                rationale=(
                    f"No type keyword; classifier {signal.model_version} predicted "
                    f"'{signal.label}' with calibrated confidence {signal.confidence:.2f}."
                ),
                decided_by="ml",
                ml=signal,
            )

        return _with_signal(await self.base.classify(message, options), signal)

    def _predict(self, message: str) -> MLSignal | None:
        if self.classifier is None or self.mode == "off":
            return None
        try:
            prediction = self.classifier.predict(message, threshold=self.threshold)
        except Exception:
            # The model is an assistant: if it fails, intake continues on the
            # rules exactly as before, and the failure is visible in the log.
            logger.exception("Complaint classifier failed; continuing with rules only")
            return None
        return _signal(prediction)

    async def extract(
        self,
        message: str,
        specs: list[FieldSpec],
        known: dict[str, str] | None = None,
        pending_field: str | None = None,
    ) -> ExtractionResult:
        return await self.base.extract(message, specs, known, pending_field=pending_field)

    async def summarize(self, transcript: list[str]) -> str:
        return await self.base.summarize(transcript)

    async def detect_language(self, message: str) -> LanguageDetection:
        return await self.base.detect_language(message)

    async def compose_reply(self, request: ReplyRequest) -> ReplyDraft:
        return await self.base.compose_reply(request)


def _signal(prediction: ClassifierPrediction) -> MLSignal:
    return MLSignal(
        label=prediction.label,
        top_label=prediction.top_label,
        confidence=round(prediction.confidence, 4),
        probabilities=prediction.probabilities,
        abstained=prediction.abstained,
        model_name=prediction.model_name,
        model_version=prediction.model_version,
        latency_ms=prediction.latency_ms,
    )


def _with_signal(result: Classification, signal: MLSignal | None) -> Classification:
    update: dict = {"ml": signal}
    if result.decided_by is None:
        update["decided_by"] = "provider"
    return result.model_copy(update=update)
