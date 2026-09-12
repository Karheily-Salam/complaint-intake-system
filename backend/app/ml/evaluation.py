"""Offline evaluation: how good is any of this, measured the same way twice.

Pure functions over predictions and labels, plus two evaluators that drive the
real code paths rather than a copy of them:

* :func:`evaluate_classification` runs a system (the keyword rules, the
  classifier alone, or the hybrid the service actually uses) over the
  held-out classification set;
* :func:`evaluate_extraction` runs the real conversation engine over the gold
  extraction set, so what is measured includes schema validation and the
  evidence check - the same behaviour a customer's email gets.

Metrics reported, and why each one:

``accuracy`` / ``macro_f1``
    Macro-F1 averages over classes, so the rarest complaint type counts as
    much as the most common one.
``per_class`` / ``confusion``
    Which type is mistaken for which - "deposit read as withdrawal" is a
    different problem from "everything read as other".
``by_language``
    The product answers in English, Russian and Arabic; an average that hides
    one of them failing is not an answer.
``ece`` and ``reliability``
    Whether a confidence of 0.8 really means right 80% of the time. The
    abstention threshold is only meaningful if it does.
``coverage`` / ``selective_accuracy``
    Of the messages the system chose to answer, how many did it get right,
    and how many did it choose to answer at all - the trade-off abstention
    exists to make.
``hallucination_rate`` (extraction)
    Values proposed with nothing in the customer's message to support them.
    The engine refuses these; this measures how often that guard fires.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

ABSTAIN = "unclear"


# ------------------------------------------------------------------ metrics


def confusion_matrix(y_true: list[str], y_pred: list[str]) -> dict[str, dict[str, int]]:
    labels = sorted(set(y_true) | set(y_pred))
    matrix = {a: dict.fromkeys(labels, 0) for a in labels}
    for actual, predicted in zip(y_true, y_pred, strict=True):
        matrix[actual][predicted] += 1
    return matrix


def per_class_scores(y_true: list[str], y_pred: list[str]) -> dict[str, dict[str, float]]:
    labels = sorted(set(y_true) | set(y_pred))
    scores = {}
    for label in labels:
        tp = sum(1 for a, p in zip(y_true, y_pred, strict=True) if a == label and p == label)
        fp = sum(1 for a, p in zip(y_true, y_pred, strict=True) if a != label and p == label)
        fn = sum(1 for a, p in zip(y_true, y_pred, strict=True) if a == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        scores[label] = {
            "support": sum(1 for a in y_true if a == label),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    return scores


def macro_f1(y_true: list[str], y_pred: list[str]) -> float:
    scores = per_class_scores(y_true, y_pred)
    return round(sum(s["f1"] for s in scores.values()) / len(scores), 4) if scores else 0.0


def accuracy(y_true: list[str], y_pred: list[str]) -> float:
    if not y_true:
        return 0.0
    correct = sum(1 for a, p in zip(y_true, y_pred, strict=True) if a == p)
    return round(correct / len(y_true), 4)


def expected_calibration_error(
    correct: list[bool], confidence: list[float], bins: int = 10
) -> tuple[float, list[dict]]:
    """ECE with equal-width bins, plus the reliability table behind it."""
    if not correct:
        return 0.0, []
    buckets: list[dict[str, Any]] = [
        {"lower": round(i / bins, 2), "upper": round((i + 1) / bins, 2), "n": 0, "correct": 0,
         "confidence_sum": 0.0}
        for i in range(bins)
    ]
    for ok, conf in zip(correct, confidence, strict=True):
        index = min(int(conf * bins), bins - 1)
        bucket = buckets[index]
        bucket["n"] += 1
        bucket["correct"] += int(ok)
        bucket["confidence_sum"] += conf
    total = len(correct)
    ece = 0.0
    table = []
    for bucket in buckets:
        if not bucket["n"]:
            continue
        acc = bucket["correct"] / bucket["n"]
        mean_conf = bucket["confidence_sum"] / bucket["n"]
        ece += (bucket["n"] / total) * abs(acc - mean_conf)
        table.append(
            {
                "range": [bucket["lower"], bucket["upper"]],
                "n": bucket["n"],
                "accuracy": round(acc, 4),
                "mean_confidence": round(mean_conf, 4),
            }
        )
    return round(ece, 4), table


def selective_scores(
    y_true: list[str], y_pred: list[str], answered: list[bool]
) -> dict[str, float]:
    """Coverage, and accuracy over the answered subset only."""
    total = len(y_true)
    covered = [i for i, ok in enumerate(answered) if ok]
    if not total:
        return {"coverage": 0.0, "selective_accuracy": 0.0, "answered": 0}
    correct = sum(1 for i in covered if y_true[i] == y_pred[i])
    return {
        "coverage": round(len(covered) / total, 4),
        "selective_accuracy": round(correct / len(covered), 4) if covered else 1.0,
        "answered": len(covered),
    }


def population_stability_index(
    reference: dict[str, int], recent: dict[str, int], epsilon: float = 1e-6
) -> float:
    """PSI between two categorical distributions.

    The standard reading: below 0.1 is no meaningful change, 0.1-0.25 is a
    moderate shift worth watching, above 0.25 is a significant one.
    """
    keys = set(reference) | set(recent)
    ref_total = max(sum(reference.values()), 1)
    rec_total = max(sum(recent.values()), 1)
    psi = 0.0
    for key in keys:
        ref_share = max(reference.get(key, 0) / ref_total, epsilon)
        rec_share = max(recent.get(key, 0) / rec_total, epsilon)
        psi += (rec_share - ref_share) * math.log(rec_share / ref_share)
    return round(psi, 4)


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; no numpy needed for a handful of latencies."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q / 100 * len(ordered)) - 1))
    return round(ordered[index], 3)


# ------------------------------------------------------------------ classification


@dataclass
class ClassificationReport:
    system: str
    n: int
    accuracy: float
    macro_f1: float
    per_class: dict[str, dict[str, float]]
    confusion: dict[str, dict[str, int]]
    by_language: dict[str, dict[str, float]]
    coverage: dict[str, float]
    ece: float | None = None
    reliability: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in vars(self).items()}


def build_classification_report(
    system: str,
    examples: list,
    predictions: list[str],
    confidences: list[float] | None = None,
    answered: list[bool] | None = None,
) -> ClassificationReport:
    y_true = [e.label for e in examples]
    answered = answered if answered is not None else [p != ABSTAIN for p in predictions]
    by_language = {}
    for language in sorted({e.language for e in examples}):
        idx = [i for i, e in enumerate(examples) if e.language == language]
        by_language[language] = {
            "n": len(idx),
            "accuracy": accuracy([y_true[i] for i in idx], [predictions[i] for i in idx]),
            "macro_f1": macro_f1([y_true[i] for i in idx], [predictions[i] for i in idx]),
        }
    ece = None
    reliability: list[dict] = []
    if confidences is not None:
        correct = [y_true[i] == predictions[i] for i in range(len(y_true))]
        ece, reliability = expected_calibration_error(correct, confidences)
    return ClassificationReport(
        system=system,
        n=len(examples),
        accuracy=accuracy(y_true, predictions),
        macro_f1=macro_f1(y_true, predictions),
        per_class=per_class_scores(y_true, predictions),
        confusion=confusion_matrix(y_true, predictions),
        by_language=by_language,
        coverage=selective_scores(y_true, predictions, answered),
        ece=ece,
        reliability=reliability,
        errors=[
            {"id": e.id, "language": e.language, "expected": e.label, "predicted": p}
            for e, p in zip(examples, predictions, strict=True)
            if e.label != p
        ],
    )


# ------------------------------------------------------------------ extraction


@dataclass
class ExtractionReport:
    n: int
    fields_expected: int
    precision: float
    recall: float
    f1: float
    exact_messages: float
    hallucination_rate: float
    rejected_values: int
    evidence_methods: dict[str, int]
    per_field: dict[str, dict[str, float]]
    by_language: dict[str, dict[str, float]]
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in vars(self).items()}


def _norm(value: str | None) -> str:
    return " ".join((value or "").split()).strip().lower()


def score_extraction(cases: list[dict]) -> ExtractionReport:
    """Score already-run extractions.

    Each case: ``{"id", "language", "gold": {...}, "predicted": {...},
    "evidence_methods": {field key: match method}, "rejected": [keys]}``. A predicted value counts
    as correct only when the key is expected *and* the value matches after
    whitespace/case normalisation.
    """
    tp = fp = fn = 0
    per_field: dict[str, Counter] = {}
    per_language: dict[str, Counter] = {}
    methods: Counter[str] = Counter()
    rejected = 0
    exact_messages = 0
    errors: list[dict] = []

    for case in cases:
        gold = {k: _norm(v) for k, v in case["gold"].items()}
        predicted = {k: _norm(v) for k, v in case["predicted"].items()}
        methods.update(case.get("evidence_methods", {}).values())
        rejected += len(case.get("rejected", []))
        language = case.get("language", "?")
        case_ok = True
        for key in set(gold) | set(predicted):
            counter = per_field.setdefault(key, Counter())
            lang_counter = per_language.setdefault(language, Counter())
            if key in gold and gold[key] == predicted.get(key):
                tp += 1
                counter["tp"] += 1
                lang_counter["tp"] += 1
            elif key in predicted and gold.get(key) != predicted[key]:
                fp += 1
                counter["fp"] += 1
                lang_counter["fp"] += 1
                case_ok = False
                errors.append(
                    {"id": case["id"], "field": key, "expected": gold.get(key),
                     "predicted": predicted[key]}
                )
                if key in gold:
                    fn += 1
                    counter["fn"] += 1
                    lang_counter["fn"] += 1
            else:
                fn += 1
                counter["fn"] += 1
                lang_counter["fn"] += 1
                case_ok = False
                errors.append(
                    {"id": case["id"], "field": key, "expected": gold.get(key), "predicted": None}
                )
        exact_messages += int(case_ok)

    def prf(counter: Counter) -> dict[str, float]:
        predicted = counter["tp"] + counter["fp"]
        expected = counter["tp"] + counter["fn"]
        precision = counter["tp"] / predicted if predicted else 0.0
        recall = counter["tp"] / expected if expected else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {
            "support": counter["tp"] + counter["fn"],
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    overall = Counter({"tp": tp, "fp": fp, "fn": fn})
    scores = prf(overall)
    proposed = tp + fp + rejected
    return ExtractionReport(
        n=len(cases),
        fields_expected=tp + fn,
        precision=scores["precision"],
        recall=scores["recall"],
        f1=scores["f1"],
        exact_messages=round(exact_messages / len(cases), 4) if cases else 0.0,
        hallucination_rate=round(rejected / proposed, 4) if proposed else 0.0,
        rejected_values=rejected,
        evidence_methods=dict(sorted(methods.items())),
        per_field={k: prf(v) for k, v in sorted(per_field.items())},
        by_language={k: prf(v) for k, v in sorted(per_language.items())},
        errors=errors,
    )
