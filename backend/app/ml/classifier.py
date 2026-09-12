"""Complaint-type classifier: calibrated linear model over hashed text features.

Why a linear model and not a transformer: it runs in about a millisecond on
one CPU core, needs no model download, handles all three scripts through
character n-grams (see :mod:`app.ml.features`), and its probabilities can be
calibrated honestly on the small dataset this project has. It sits in the
latency-sensitive email path, where a heavier model would cost more than it
could add.

Three things make its output safe to act on:

* **Calibration.** Raw softmax scores of a regularised linear model are not
  probabilities. A temperature fitted on out-of-fold predictions (see
  :mod:`app.ml.training`) rescales them so "0.8" means right about 80% of the
  time on held-out data.
* **An explicit "unclear" class.** Greetings and one-line "I have a problem"
  messages are a class of their own in training, so the model learns to say
  "too vague" instead of forcing one of the three real types.
* **Abstention.** Below the confidence threshold chosen from the
  risk-coverage curve, the prediction is withheld. The engine then asks the
  customer, exactly as it does when the rules cannot decide.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.ml.features import HashingVectorizer

ABSTAIN_LABEL = "unclear"
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
DEFAULT_ARTIFACT = ARTIFACT_DIR / "complaint_classifier"
MODEL_NAME = "complaint-type-linear"


@dataclass(frozen=True)
class ClassifierPrediction:
    """One prediction. ``label`` is None when the model abstains."""

    label: str | None
    top_label: str
    confidence: float
    probabilities: dict[str, float]
    abstained: bool
    model_name: str
    model_version: str
    latency_ms: float


def softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    scaled = logits / max(temperature, 1e-6)
    scaled = scaled - scaled.max(axis=-1, keepdims=True)
    exp = np.exp(scaled)
    return exp / exp.sum(axis=-1, keepdims=True)


@dataclass
class LinearTextClassifier:
    vectorizer: HashingVectorizer
    weights: np.ndarray  # (n_classes, dim)
    bias: np.ndarray  # (n_classes,)
    labels: list[str]
    temperature: float = 1.0
    threshold: float = 0.5
    metadata: dict = field(default_factory=dict)

    @property
    def version(self) -> str:
        return str(self.metadata.get("version") or "unversioned")

    def logits(self, text: str) -> np.ndarray:
        indices, values = self.vectorizer.transform_one(text)
        return self.weights[:, indices] @ values + self.bias

    def logits_matrix(self, x: np.ndarray) -> np.ndarray:
        return x @ self.weights.T + self.bias

    def predict_proba(self, text: str) -> dict[str, float]:
        probs = softmax(self.logits(text), self.temperature)
        return {label: float(p) for label, p in zip(self.labels, probs, strict=True)}

    def predict(self, text: str, threshold: float | None = None) -> ClassifierPrediction:
        started = time.perf_counter()
        probs = softmax(self.logits(text), self.temperature)
        best = int(np.argmax(probs))
        top_label = self.labels[best]
        confidence = float(probs[best])
        cutoff = self.threshold if threshold is None else threshold
        abstained = top_label == ABSTAIN_LABEL or confidence < cutoff
        return ClassifierPrediction(
            label=None if abstained else top_label,
            top_label=top_label,
            confidence=confidence,
            probabilities={
                label: round(float(p), 4) for label, p in zip(self.labels, probs, strict=True)
            },
            abstained=abstained,
            model_name=MODEL_NAME,
            model_version=self.version,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    # ------------------------------------------------------------------ persistence

    def save(self, base: Path) -> None:
        """Write ``<base>.npz`` (weights) and ``<base>.json`` (everything else)."""
        base.parent.mkdir(parents=True, exist_ok=True)
        weights = self.weights.astype(np.float32)
        bias = self.bias.astype(np.float32)
        digest = hashlib.sha256(weights.tobytes() + bias.tobytes()).hexdigest()
        meta = dict(self.metadata)
        meta.update(
            {
                "model_name": MODEL_NAME,
                "labels": self.labels,
                "temperature": self.temperature,
                "threshold": self.threshold,
                "vectorizer": self.vectorizer.config(),
                "weights_sha256": digest,
            }
        )
        meta["version"] = f"{meta.get('semver', '1.0.0')}+{digest[:8]}"
        self.metadata = meta
        np.savez_compressed(base.with_suffix(".npz"), weights=weights, bias=bias)
        base.with_suffix(".json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, base: Path = DEFAULT_ARTIFACT) -> LinearTextClassifier:
        meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
        with np.load(base.with_suffix(".npz")) as arrays:
            weights = arrays["weights"]
            bias = arrays["bias"]
        digest = hashlib.sha256(weights.tobytes() + bias.tobytes()).hexdigest()
        if digest != meta.get("weights_sha256"):
            raise ValueError(f"Classifier artifact {base} does not match its metadata")
        return cls(
            vectorizer=HashingVectorizer.from_config(meta["vectorizer"]),
            weights=weights,
            bias=bias,
            labels=list(meta["labels"]),
            temperature=float(meta["temperature"]),
            threshold=float(meta["threshold"]),
            metadata=meta,
        )


@lru_cache
def get_classifier() -> LinearTextClassifier | None:
    """The shipped model, or None if the artifact is missing or corrupt.

    A missing model must never take intake down: the hybrid provider simply
    falls back to the rules, which is exactly the pre-ML behaviour.
    """
    try:
        return LinearTextClassifier.load(DEFAULT_ARTIFACT)
    except (OSError, ValueError, KeyError):
        from app.core.logging import get_logger

        get_logger(__name__).exception("Complaint classifier artifact unavailable")
        return None
