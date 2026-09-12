"""Offline training for :class:`~app.ml.classifier.LinearTextClassifier`.

Never imported by the running service - only by ``scripts/train_classifier.py``
and the tests. Everything is deterministic (zero initialisation, full-batch
optimisation, seeded fold assignment), so the same data always produces the
same weights and the same artifact version.

Procedure:

1. Multinomial logistic regression (softmax + L2), trained with full-batch
   Adam. With a few hundred examples a full batch is both exact and fast.
2. Stratified k-fold cross-validation picks the L2 strength and produces
   out-of-fold (OOF) logits - predictions for every example from a model that
   never saw it.
3. A temperature is fitted to the OOF logits by minimising negative
   log-likelihood (temperature scaling).
4. The abstention threshold is the lowest confidence at which OOF selective
   accuracy reaches the target - read off the risk-coverage curve.
5. The final model is trained on all data with the chosen settings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.ml.classifier import ABSTAIN_LABEL, LinearTextClassifier, softmax
from app.ml.features import HashingVectorizer


@dataclass(frozen=True)
class Example:
    id: str
    language: str
    label: str
    text: str


def load_examples(path: Path) -> list[Example]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            data = json.loads(line)
            rows.append(
                Example(
                    id=data["id"], language=data["language"], label=data["label"],
                    text=data["text"],
                )
            )
    return rows


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def train_softmax(
    x: np.ndarray,
    y: np.ndarray,
    n_classes: int,
    *,
    l2: float,
    epochs: int = 300,
    lr: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    """Full-batch Adam on the L2-regularised softmax cross-entropy."""
    n, dim = x.shape
    w = np.zeros((n_classes, dim), dtype=np.float32)
    b = np.zeros(n_classes, dtype=np.float32)
    onehot = np.eye(n_classes, dtype=np.float32)[y]
    mw = np.zeros_like(w)
    vw = np.zeros_like(w)
    mb = np.zeros_like(b)
    vb = np.zeros_like(b)
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    for step in range(1, epochs + 1):
        probs = softmax(x @ w.T + b)
        diff = (probs - onehot) / n
        gw = diff.T @ x + l2 * w
        gb = diff.sum(axis=0)
        mw = beta1 * mw + (1 - beta1) * gw
        vw = beta2 * vw + (1 - beta2) * gw * gw
        mb = beta1 * mb + (1 - beta1) * gb
        vb = beta2 * vb + (1 - beta2) * gb * gb
        correction1 = 1 - beta1**step
        correction2 = 1 - beta2**step
        w -= lr * (mw / correction1) / (np.sqrt(vw / correction2) + eps)
        b -= lr * (mb / correction1) / (np.sqrt(vb / correction2) + eps)
    return w, b


def stratified_folds(labels: np.ndarray, k: int, seed: int = 13) -> np.ndarray:
    """Fold id per example, each class spread evenly over the folds."""
    rng = np.random.default_rng(seed)
    folds = np.empty(len(labels), dtype=np.int64)
    for cls in np.unique(labels):
        idx = np.flatnonzero(labels == cls)
        rng.shuffle(idx)
        folds[idx] = np.arange(len(idx)) % k
    return folds


def nll(logits: np.ndarray, y: np.ndarray, temperature: float) -> float:
    probs = softmax(logits, temperature)
    return float(-np.mean(np.log(probs[np.arange(len(y)), y] + 1e-12)))


def fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    grid = np.exp(np.linspace(np.log(0.02), np.log(20.0), 400))
    losses = [nll(logits, y, t) for t in grid]
    return float(grid[int(np.argmin(losses))])


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int) -> float:
    scores = []
    for c in range(n_classes):
        tp = int(np.sum((y_pred == c) & (y_true == c)))
        fp = int(np.sum((y_pred == c) & (y_true != c)))
        fn = int(np.sum((y_pred != c) & (y_true == c)))
        denom = 2 * tp + fp + fn
        scores.append(2 * tp / denom if denom else 0.0)
    return float(np.mean(scores))


def choose_threshold(
    probs: np.ndarray, y: np.ndarray, abstain_index: int, target_accuracy: float
) -> tuple[float, dict]:
    """Lowest threshold whose selective accuracy meets the target.

    A prediction is *answered* when its top class is a real type (not
    "unclear") and its confidence clears the threshold; it is *correct* when
    that answer matches the label. Wrongly answering a truly vague message
    counts as an error - that is exactly the mistake abstention exists to
    prevent.
    """
    top = probs.argmax(axis=1)
    conf = probs.max(axis=1)
    curve = []
    chosen = None
    for t in np.round(np.arange(0.30, 0.96, 0.01), 2):
        answered = (top != abstain_index) & (conf >= t)
        coverage = float(answered.mean())
        accuracy = float((top[answered] == y[answered]).mean()) if answered.any() else 1.0
        curve.append({"threshold": float(t), "coverage": coverage, "accuracy": accuracy})
        if chosen is None and accuracy >= target_accuracy:
            chosen = float(t)
    return (chosen if chosen is not None else 0.95), {"risk_coverage": curve}


@dataclass
class TrainingReport:
    l2: float
    temperature: float
    threshold: float
    cv: dict
    curve: list


def train_classifier(
    examples: list[Example],
    *,
    dim: int = 1 << 15,
    l2_grid: tuple[float, ...] = (1e-4, 1e-3, 1e-2),
    folds: int = 5,
    epochs: int = 300,
    target_accuracy: float = 0.9,
    dataset_sha256: str | None = None,
) -> tuple[LinearTextClassifier, TrainingReport]:
    labels = sorted({e.label for e in examples})
    if ABSTAIN_LABEL not in labels:
        raise ValueError(f"training data needs '{ABSTAIN_LABEL}' examples to learn abstention")
    index = {label: i for i, label in enumerate(labels)}
    vectorizer = HashingVectorizer(dim=dim)
    x = vectorizer.transform([e.text for e in examples])
    y = np.array([index[e.label] for e in examples])
    fold_of = stratified_folds(y, folds)

    best = None
    for l2 in l2_grid:
        oof = np.zeros((len(y), len(labels)), dtype=np.float32)
        for k in range(folds):
            train, held = fold_of != k, fold_of == k
            w, b = train_softmax(x[train], y[train], len(labels), l2=l2, epochs=epochs)
            oof[held] = x[held] @ w.T + b
        score = macro_f1(y, oof.argmax(axis=1), len(labels))
        if best is None or score > best[1]:
            best = (l2, score, oof)
    assert best is not None
    l2, cv_f1, oof_logits = best

    temperature = fit_temperature(oof_logits, y)
    oof_probs = softmax(oof_logits, temperature)
    threshold, extra = choose_threshold(oof_probs, y, index[ABSTAIN_LABEL], target_accuracy)
    threshold = max(threshold, 0.5)

    w, b = train_softmax(x, y, len(labels), l2=l2, epochs=epochs)
    model = LinearTextClassifier(
        vectorizer=vectorizer,
        weights=w,
        bias=b,
        labels=labels,
        temperature=temperature,
        threshold=threshold,
        metadata={
            "semver": "1.0.0",
            "training": {
                "examples": len(examples),
                "by_label": {lab: int((y == i).sum()) for lab, i in index.items()},
                "by_language": _count(e.language for e in examples),
                "dataset_sha256": dataset_sha256,
                "l2": l2,
                "l2_grid": list(l2_grid),
                "epochs": epochs,
                "folds": folds,
                "target_selective_accuracy": target_accuracy,
            },
            "cross_validation": {
                "macro_f1": round(cv_f1, 4),
                "nll_uncalibrated": round(nll(oof_logits, y, 1.0), 4),
                "nll_calibrated": round(nll(oof_logits, y, temperature), 4),
            },
        },
    )
    report = TrainingReport(
        l2=l2,
        temperature=temperature,
        threshold=threshold,
        cv=model.metadata["cross_validation"],
        curve=extra["risk_coverage"],
    )
    return model, report


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))
