"""Evaluation: the metrics themselves, the measured quality of the shipped
models, and that the committed report still describes them.

The quality assertions are floors, not exact numbers: they fail when a change
makes the system measurably worse, without breaking every time a model is
retrained. The one exact claim is precision on extraction - an invented value
must never reach a ticket.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ml.classifier import get_classifier
from app.ml.evaluation import (
    accuracy,
    confusion_matrix,
    expected_calibration_error,
    macro_f1,
    per_class_scores,
    percentile,
    population_stability_index,
    score_extraction,
    selective_scores,
)
from app.ml.evaluation_runner import (
    CLASSIFICATION_TEST,
    EXTRACTION_TEST,
    evaluate_classification,
    evaluate_extraction,
    full_report,
    load_extraction_cases,
)
from app.ml.training import file_sha256, load_examples
from tests.conftest import BACKEND_DIR

REPORT = BACKEND_DIR.parent / "docs" / "ml" / "evaluation.json"


# ------------------------------------------------------------------ metrics


def test_confusion_and_per_class_scores():
    y_true = ["a", "a", "b", "b", "c"]
    y_pred = ["a", "b", "b", "b", "a"]
    matrix = confusion_matrix(y_true, y_pred)
    assert matrix["a"] == {"a": 1, "b": 1, "c": 0}
    scores = per_class_scores(y_true, y_pred)
    assert scores["b"]["precision"] == pytest.approx(2 / 3, abs=1e-4)
    assert scores["b"]["recall"] == 1.0
    assert scores["c"]["f1"] == 0.0
    assert accuracy(y_true, y_pred) == 0.6
    assert macro_f1(y_true, y_pred) == pytest.approx((0.5 + 0.8 + 0.0) / 3, abs=1e-3)


def test_calibration_error_rewards_honest_confidence():
    honest_correct = [True] * 8 + [False] * 2
    honest_conf = [0.82] * 10
    honest, table = expected_calibration_error(honest_correct, honest_conf)
    assert honest < 0.05 and table[0]["n"] == 10

    overconfident, _ = expected_calibration_error(honest_correct, [0.99] * 10)
    assert overconfident > honest


def test_selective_scores_describe_the_abstention_trade_off():
    result = selective_scores(
        ["a", "b", "c"], ["a", "b", "unclear"], answered=[True, True, False]
    )
    assert result == {"coverage": pytest.approx(2 / 3, abs=1e-4), "selective_accuracy": 1.0,
                      "answered": 2}


def test_psi_flags_a_shifted_distribution():
    same = {"withdrawal": 50, "deposit": 30, "other": 20}
    assert population_stability_index(same, dict(same)) == 0.0
    shifted = {"withdrawal": 5, "deposit": 5, "other": 90}
    assert population_stability_index(same, shifted) > 0.25


def test_percentiles_without_numpy():
    assert percentile([], 50) is None
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0


def test_extraction_scoring_counts_a_wrong_value_as_both_miss_and_false_alarm():
    report = score_extraction(
        [
            {"id": "1", "language": "en", "gold": {"user_id": "U-1"},
             "predicted": {"user_id": "U-1"}, "evidence_methods": {"user_id": "exact"}},
            {"id": "2", "language": "en", "gold": {"user_id": "U-2"},
             "predicted": {"user_id": "U-9"}, "evidence_methods": {"user_id": "exact"}},
            {"id": "3", "language": "ru", "gold": {"user_id": "U-3"}, "predicted": {},
             "rejected": ["user_id"]},
        ]
    )
    assert report.precision == 0.5 and report.recall == pytest.approx(1 / 3, abs=1e-4)
    assert report.exact_messages == pytest.approx(1 / 3, abs=1e-4)
    assert report.hallucination_rate == pytest.approx(1 / 3, abs=1e-4)
    assert report.evidence_methods == {"exact": 2}


# ------------------------------------------------------------------ measured quality


@pytest.fixture(scope="module")
def classification_examples():
    return load_examples(CLASSIFICATION_TEST)


async def test_the_model_beats_the_rules_and_the_hybrid_beats_both(classification_examples):
    rules = await evaluate_classification("rules", classification_examples)
    ml = await evaluate_classification("ml", classification_examples)
    hybrid = await evaluate_classification("hybrid", classification_examples)

    assert ml.macro_f1 > rules.macro_f1, "the model must earn its place against the baseline"
    assert hybrid.macro_f1 >= ml.macro_f1
    assert hybrid.macro_f1 >= 0.75, f"regression: hybrid macro-F1 fell to {hybrid.macro_f1}"
    assert rules.macro_f1 >= 0.30, "the deterministic baseline should not silently break"


async def test_answers_given_are_overwhelmingly_right(classification_examples):
    hybrid = await evaluate_classification("hybrid", classification_examples)
    assert hybrid.coverage["selective_accuracy"] >= 0.9
    assert hybrid.coverage["coverage"] >= 0.4, "abstaining on everything is not a solution"


async def test_every_language_is_measured_and_none_collapses(classification_examples):
    hybrid = await evaluate_classification("hybrid", classification_examples)
    assert set(hybrid.by_language) == {"en", "ru", "ar"}
    for language, scores in hybrid.by_language.items():
        assert scores["macro_f1"] >= 0.55, f"{language} macro-F1 {scores['macro_f1']}"


async def test_extraction_never_invents_a_value():
    report = await evaluate_extraction(load_extraction_cases())
    assert report.precision == 1.0, f"a wrong value reached a ticket: {report.errors}"
    assert report.hallucination_rate == 0.0
    assert report.recall >= 0.8
    assert set(report.by_language) == {"en", "ru", "ar"}


async def test_the_evaluation_is_reproducible():
    first = await full_report()
    second = await full_report()
    assert first == second


# ------------------------------------------------------------------ the committed report


def test_the_committed_report_matches_the_current_datasets_and_model():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["datasets"]["classification_test"]["sha256"] == file_sha256(CLASSIFICATION_TEST)
    assert report["datasets"]["extraction_test"]["sha256"] == file_sha256(EXTRACTION_TEST)
    assert report["models"]["classifier"]["version"] == get_classifier().version, (
        "docs/ml/evaluation.json is stale - rerun backend/scripts/evaluate_ml.py"
    )


def test_the_markdown_report_exists_and_names_the_baseline():
    text = (Path(REPORT).with_suffix(".md")).read_text(encoding="utf-8")
    assert "Keyword rules (the baseline)" in text and "Hallucination rate" in text
