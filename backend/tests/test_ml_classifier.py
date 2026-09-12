"""Complaint-type classification: features, training, calibration, abstention,
the hybrid decision order, and the prediction log.

The decision-order tests use a stub classifier so they state the policy
itself ("a keyword always wins") independently of what the shipped model
happens to predict. The tests on the shipped model pin a handful of
behaviours that matter - greetings abstain, keyword-free complaints in all
three languages are recognised - rather than exact probabilities.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from app.ai.base import Classification, TypeOption
from app.ai.providers.hybrid import HybridAIProvider
from app.ai.providers.rule_based import RuleBasedAIProvider
from app.db.models.ml_prediction import MLPrediction
from app.ml import classifier as classifier_module
from app.ml.classifier import (
    ABSTAIN_LABEL,
    DEFAULT_ARTIFACT,
    ClassifierPrediction,
    LinearTextClassifier,
    softmax,
)
from app.ml.features import HashingVectorizer, normalize, tokenize
from app.ml.training import (
    Example,
    choose_threshold,
    file_sha256,
    fit_temperature,
    load_examples,
    train_classifier,
    train_softmax,
)
from tests.conftest import BACKEND_DIR

DATASETS = BACKEND_DIR / "datasets" / "complaints"
OPTIONS = [
    TypeOption(type="withdrawal", label="Withdrawal"),
    TypeOption(type="deposit", label="Deposit"),
    TypeOption(type="other", label="Other"),
]


@pytest.fixture(scope="module")
def model() -> LinearTextClassifier:
    return LinearTextClassifier.load(DEFAULT_ARTIFACT)


# ------------------------------------------------------------------ features


def test_normalisation_folds_meaningless_variation():
    assert normalize("ЁЛКА") == "елка"
    # hamza-carrying alef, alef maqsura and ta marbuta fold; harakat vanish
    assert normalize("إيداعٌ") == normalize("ايداع")
    assert normalize("مستشفى") == normalize("مستشفي")


def test_identifiers_never_become_features():
    tokens = tokenize("user U-482913 wrote from jane.doe@example.com, order 99812")
    assert "jane" not in tokens and "482913" not in tokens and "99812" not in tokens
    assert "email" in tokens


def test_hashing_is_stable_and_normalised():
    vec = HashingVectorizer(dim=1 << 12)
    a_idx, a_val = vec.transform_one("My withdrawal is stuck")
    b_idx, b_val = vec.transform_one("My withdrawal is stuck")
    assert np.array_equal(a_idx, b_idx) and np.array_equal(a_val, b_val)
    assert np.isclose(np.linalg.norm(a_val), 1.0)
    empty_idx, _ = vec.transform_one("   ")
    assert empty_idx.size == 0


# ------------------------------------------------------------------ training


def test_softmax_regression_learns_a_separable_problem():
    x = np.array([[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9]], dtype=np.float32)
    y = np.array([0, 0, 1, 1])
    w, b = train_softmax(x, y, 2, l2=1e-4, epochs=200, lr=0.1)
    assert (softmax(x @ w.T + b).argmax(axis=1) == y).all()


def test_temperature_scaling_softens_overconfident_logits():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 3, size=300)
    logits = rng.normal(size=(300, 3))
    logits[np.arange(300), y] += 1.0  # somewhat informative
    assert fit_temperature(logits * 8, y) > 2.0, "8x-inflated logits need T well above 1"


def test_threshold_is_the_lowest_meeting_the_target_accuracy():
    # Two classes plus 'unclear' (index 2). Confident answers are right,
    # unsure ones are wrong - so accuracy jumps once the unsure are excluded.
    probs = np.array([[0.95, 0.03, 0.02]] * 8 + [[0.55, 0.40, 0.05]] * 2)
    y = np.array([0] * 8 + [1] * 2)
    threshold, extra = choose_threshold(probs, y, abstain_index=2, target_accuracy=0.99)
    assert 0.55 < threshold <= 0.95
    assert extra["risk_coverage"][0]["coverage"] == 1.0


def test_training_is_deterministic():
    examples = load_examples(DATASETS / "train.jsonl")[::4]
    first, _ = train_classifier(examples, dim=1 << 12, l2_grid=(1e-3,), folds=3, epochs=60)
    second, _ = train_classifier(examples, dim=1 << 12, l2_grid=(1e-3,), folds=3, epochs=60)
    assert np.array_equal(first.weights, second.weights)
    assert first.temperature == second.temperature


def test_training_refuses_data_without_an_abstain_class():
    examples = [Example("a", "en", "deposit", "paid in"), Example("b", "en", "other", "login")]
    with pytest.raises(ValueError, match=ABSTAIN_LABEL):
        train_classifier(examples)


# ------------------------------------------------------------------ the shipped artifact


def test_artifact_was_trained_on_the_current_training_data(model):
    recorded = model.metadata["training"]["dataset_sha256"]
    assert recorded == file_sha256(DATASETS / "train.jsonl"), (
        "datasets/complaints/train.jsonl changed but the model was not retrained - "
        "run scripts/train_classifier.py"
    )


def test_no_test_sentence_leaks_into_training():
    train = {e.text.strip().lower() for e in load_examples(DATASETS / "train.jsonl")}
    test = [e.text.strip().lower() for e in load_examples(DATASETS / "test.jsonl")]
    assert not [t for t in test if t in train]


def test_artifact_is_verified_on_load(tmp_path):
    base = tmp_path / "clf"
    for suffix in (".npz", ".json"):
        base.with_suffix(suffix).write_bytes(DEFAULT_ARTIFACT.with_suffix(suffix).read_bytes())
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    meta["weights_sha256"] = "0" * 64
    base.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        LinearTextClassifier.load(base)


def test_probabilities_are_a_distribution(model):
    probs = model.predict_proba("my withdrawal never arrived")
    assert set(probs) == {"deposit", "other", "unclear", "withdrawal"}
    assert abs(sum(probs.values()) - 1.0) < 1e-5


@pytest.mark.parametrize("text", ["Hello", "Здравствуйте", "مرحبا", "Can someone help me?"])
def test_greetings_abstain(model, text):
    prediction = model.predict(text)
    assert prediction.abstained and prediction.label is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # None of these contains a rule keyword, which is the case the model is for.
        ("I paid but my account still shows the old balance.", "deposit"),
        ("Выплата в ожидании уже десять дней.", "withdrawal"),
        ("لم أحصل على مكافأة الترحيب المعلن عنها.", "other"),
    ],
)
def test_keyword_free_complaints_are_recognised(model, text, expected):
    assert model.predict(text).top_label == expected


def test_prediction_latency_is_recorded_and_small(model):
    prediction = model.predict("Не могу войти в аккаунт, пишет неверный пароль.")
    assert prediction.model_version == model.version
    assert 0 <= prediction.latency_ms < 200


# ------------------------------------------------------------------ hybrid decision order


class StubClassifier:
    """Always predicts the same thing - lets each test state the policy alone."""

    def __init__(self, label: str | None, confidence: float = 0.97, top: str | None = None):
        self._p = ClassifierPrediction(
            label=label,
            top_label=top or label or ABSTAIN_LABEL,
            confidence=confidence,
            probabilities={},
            abstained=label is None,
            model_name="stub",
            model_version="stub-1",
            latency_ms=0.1,
        )

    def predict(self, text, threshold=None):
        return self._p


class ExplodingClassifier:
    def predict(self, text, threshold=None):
        raise RuntimeError("model broke")


def hybrid(classifier, mode="assist"):
    return HybridAIProvider(RuleBasedAIProvider(), classifier, mode=mode)


async def test_a_keyword_match_always_wins_over_the_model():
    result = await hybrid(StubClassifier("deposit")).classify(
        "My withdrawal has not arrived", OPTIONS
    )
    assert result.type == "withdrawal" and result.decided_by == "rules"
    assert result.ml is not None and result.ml.label == "deposit", "disagreement recorded"


async def test_the_model_decides_only_when_the_rules_find_nothing():
    result = await hybrid(StubClassifier("deposit")).classify(
        "I paid but my balance is the same", OPTIONS
    )
    assert result.type == "deposit" and result.decided_by == "ml"
    assert result.confidence == pytest.approx(0.97)


async def test_an_abstaining_model_falls_back_to_the_rules():
    result = await hybrid(StubClassifier(None)).classify("hello", OPTIONS)
    assert result.type is None and result.decided_by == "rules"
    assert result.ml is not None and result.ml.abstained


async def test_shadow_mode_never_changes_the_decision():
    result = await hybrid(StubClassifier("deposit"), mode="shadow").classify(
        "I paid but my balance is the same", OPTIONS
    )
    rules_only = await RuleBasedAIProvider().classify("I paid but my balance is the same", OPTIONS)
    assert result.type == rules_only.type
    assert result.ml is not None and result.ml.label == "deposit"


async def test_off_mode_does_not_consult_the_model():
    result = await hybrid(ExplodingClassifier(), mode="off").classify("hello", OPTIONS)
    assert result.ml is None


async def test_a_failing_model_degrades_to_the_rules():
    result = await hybrid(ExplodingClassifier()).classify("I paid but nothing", OPTIONS)
    assert result.ml is None and result.decided_by == "rules"


async def test_a_label_outside_the_offered_types_is_ignored():
    only_two = OPTIONS[:2]
    result = await hybrid(StubClassifier("other")).classify("my app keeps crashing", only_two)
    assert result.decided_by != "ml"


async def test_no_artifact_means_rules_only(monkeypatch, tmp_path):
    monkeypatch.setattr(classifier_module, "DEFAULT_ARTIFACT", tmp_path / "missing")
    classifier_module.get_classifier.cache_clear()
    try:
        assert classifier_module.get_classifier() is None
        result = await hybrid(None).classify("hello", OPTIONS)
        assert result.type is None and result.ml is None
    finally:
        classifier_module.get_classifier.cache_clear()


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        HybridAIProvider(RuleBasedAIProvider(), None, mode="yolo")


# ------------------------------------------------------------------ end to end


def send(client, body, **extra):
    payload = {"from_addr": "ml-test@example.com", "body": body, **extra}
    response = client.post("/api/v1/inbox", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_russian_complaint_without_keywords_is_classified(client, db_session):
    # The rules cannot place this (their "other" signal is English-only), so
    # before the model it was answered with a clarifying question.
    result = send(client, "Не могу войти в аккаунт, пишет неверный пароль даже после сброса.")
    assert result["complaint_type"] == "other"

    row = db_session.scalar(select(MLPrediction).where(MLPrediction.task == "classification"))
    assert row is not None
    assert row.decided_by == "ml" and row.final_label == "other"
    assert row.model_name == "complaint-type-linear" and row.is_demo is True
    assert row.language_code == "ru" and row.latency_ms is not None


def test_prediction_rows_hold_no_message_content(client, db_session):
    body = "My withdrawal is stuck, user id U-555123, email jane@example.com"
    send(client, body)
    rows = db_session.scalars(select(MLPrediction)).all()
    assert rows
    for row in rows:
        blob = json.dumps(
            {c.name: getattr(row, c.name) for c in row.__table__.columns}, default=str
        )
        assert "U-555123" not in blob and "jane@example.com" not in blob
        assert "stuck" not in blob


def test_the_rules_decision_is_logged_with_the_models_opinion(client, db_session):
    send(client, "My withdrawal has not arrived after five days")
    row = db_session.scalar(select(MLPrediction).where(MLPrediction.task == "classification"))
    assert row.decided_by == "rules" and row.final_label == "withdrawal"
    assert row.details["probabilities"], "the model's opinion is kept for evaluation"


def test_classification_is_logged_only_while_the_type_is_unknown(client, db_session):
    first = send(client, "My withdrawal has not arrived after five days")
    send(client, "user id U-777", conversation_id=first["conversation"]["id"])
    rows = db_session.scalars(select(MLPrediction).where(MLPrediction.task == "classification"))
    assert len(list(rows)) == 1


def test_stub_prediction_dataclass_is_frozen():
    p = StubClassifier("deposit").predict("x")
    assert replace(p, label=None).label is None


def test_classification_model_keeps_backward_compatible_defaults():
    legacy = Classification(type="deposit", confidence=0.8)
    assert legacy.decided_by is None and legacy.ml is None


def test_dataset_files_exist_and_are_multilingual():
    for name in ("train.jsonl", "test.jsonl"):
        rows = load_examples(Path(DATASETS / name))
        assert {r.language for r in rows} == {"en", "ru", "ar"}
        assert {r.label for r in rows} == {"withdrawal", "deposit", "other", "unclear"}
