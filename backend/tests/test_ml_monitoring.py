"""Live model monitoring: registry, prediction stats, shadow agreement, drift.

Rows are written straight into ml_predictions so each statistic can be posed
as a question with a known answer, and one test drives real intake to confirm
the numbers describe what actually happens.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.db.models.ml_prediction import MLPrediction
from app.ml.monitoring import model_registry, monitoring_snapshot

NOW = datetime.now(UTC).replace(tzinfo=None)


def add_prediction(
    db,
    *,
    task="classification",
    decided_by="rules",
    final_label="withdrawal",
    top_label="withdrawal",
    confidence=0.9,
    abstained=False,
    language="en",
    is_demo=False,
    age_days=0.0,
    latency_ms=2.0,
    details=None,
):
    row = MLPrediction(
        task=task,
        model_name="complaint-type-linear",
        model_version="1.0.0+test",
        conversation_id=None,
        message_id=None,
        is_demo=is_demo,
        language_code=language,
        predicted_label=top_label,
        confidence=confidence,
        abstained=abstained,
        final_label=final_label,
        decided_by=decided_by,
        latency_ms=latency_ms,
        details=details if details is not None else {"top_label": top_label},
        created_at=NOW - timedelta(days=age_days),
    )
    db.add(row)
    db.flush()
    return row


# ------------------------------------------------------------------ registry


def test_the_registry_names_every_active_model_and_policy():
    registry = model_registry()
    assert registry["classifier"]["available"] is True
    assert registry["classifier"]["mode"] == "assist"
    assert registry["classifier"]["labels"] == ["deposit", "other", "unclear", "withdrawal"]
    assert 0.5 <= registry["classifier"]["threshold"] <= 0.95
    assert registry["embedder"]["name"] == "hashing-char-ngram"
    assert registry["embedder"]["fallback_active"] is False
    assert registry["extraction"] == {"evidence_policy": "evidence-v1", "require_evidence": True}


# ------------------------------------------------------------------ snapshot


def test_volumes_abstention_and_latency(db_session):
    for _ in range(3):
        add_prediction(db_session, confidence=0.95)
    add_prediction(db_session, decided_by="rules", abstained=True, confidence=0.2, latency_ms=8.0)
    add_prediction(db_session, task="extraction", decided_by="rules",
                   details={"proposed": 3, "accepted": {"user_id": "exact"}, "rejected": ["x"]},
                   final_label=None, top_label=None, confidence=None)
    db_session.commit()

    snapshot = monitoring_snapshot(db_session, days=7)
    assert snapshot["predictions"]["total"] == 5
    assert snapshot["predictions"]["by_task"] == {"classification": 4, "extraction": 1}
    assert snapshot["classification"]["abstention_rate"] == 0.25
    assert snapshot["classification"]["latency_ms"]["p50"] == 2.0
    assert snapshot["classification"]["confidence"]["buckets"]["0.85-1"] == 3
    assert snapshot["extraction"]["values_proposed"] == 3
    assert snapshot["extraction"]["rejection_rate"] == round(1 / 3, 4)
    assert snapshot["extraction"]["evidence_methods"] == {"exact": 1}


def test_shadow_agreement_compares_the_model_with_what_was_decided(db_session):
    # Three decisions made by the rules; the model agreed with two of them.
    add_prediction(db_session, decided_by="rules", final_label="withdrawal", top_label="withdrawal")
    add_prediction(db_session, decided_by="rules", final_label="withdrawal", top_label="withdrawal")
    add_prediction(db_session, decided_by="rules", final_label="withdrawal", top_label="deposit")
    # A decision the model itself made is not evidence about the model.
    add_prediction(db_session, decided_by="ml", final_label="other", top_label="other")
    db_session.commit()

    agreement = monitoring_snapshot(db_session, days=7)["classification"]["shadow_agreement"]
    assert agreement == {"compared": 3, "agreed": 2, "rate": round(2 / 3, 4)}


def test_demo_traffic_is_excluded_unless_asked(db_session):
    add_prediction(db_session, is_demo=True)
    add_prediction(db_session, is_demo=False)
    db_session.commit()
    assert monitoring_snapshot(db_session, days=7)["predictions"]["total"] == 1
    assert monitoring_snapshot(db_session, days=7, include_demo=True)["predictions"]["total"] == 2


def test_drift_needs_two_windows_and_notices_a_shift(db_session):
    assert monitoring_snapshot(db_session, days=7)["drift"]["status"] == "insufficient_data"

    for _ in range(20):
        add_prediction(db_session, final_label="withdrawal", age_days=9)
    for _ in range(20):
        add_prediction(db_session, final_label="withdrawal", age_days=1)
    db_session.commit()
    assert monitoring_snapshot(db_session, days=7)["drift"]["status"] == "stable"

    for _ in range(40):
        add_prediction(db_session, final_label="other", age_days=1)
    db_session.commit()
    drift = monitoring_snapshot(db_session, days=7)["drift"]
    assert drift["label_psi"] > 0.25 and drift["status"] == "significant_shift"


def test_the_snapshot_carries_no_customer_data(client, db_session):
    client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "monitor@example.com",
            "body": "My withdrawal never arrived. user id U-482913, "
            "account email jane.doe@example.com, transaction TXN-9f3a12bc.",
        },
    )
    blob = json.dumps(monitoring_snapshot(db_session, days=7, include_demo=True), default=str)
    for secret in ("U-482913", "jane.doe@example.com", "TXN-9f3a12bc", "withdrawal never"):
        assert secret not in blob


def test_real_intake_shows_up_in_the_snapshot(client, db_session):
    client.post(
        "/api/v1/inbox",
        json={"from_addr": "live@example.com", "body": "My withdrawal has not arrived in 5 days"},
    )
    snapshot = monitoring_snapshot(db_session, days=7, include_demo=True)
    assert snapshot["classification"]["n"] == 1
    assert snapshot["classification"]["by_decided_by"] == {"rules": 1}
    assert snapshot["classification"]["shadow_agreement"]["compared"] == 1
    assert snapshot["extraction"]["n"] >= 1
    assert snapshot["models"]["classifier"]["available"] is True
    assert snapshot["worker"]["runs"] >= 0


# ------------------------------------------------------------------ API


def test_monitoring_endpoints(staff_client):
    models = staff_client.get("/api/v1/ml/models")
    assert models.status_code == 200
    assert models.json()["classifier"]["mode"] == "assist"

    monitoring = staff_client.get("/api/v1/ml/monitoring?days=7")
    assert monitoring.status_code == 200
    body = monitoring.json()
    assert body["window_days"] == 7 and "drift" in body and "feedback" in body

    assert staff_client.get("/api/v1/ml/monitoring?days=0").status_code == 422


def test_monitoring_endpoints_are_staff_only(client):
    assert client.get("/api/v1/ml/monitoring").status_code == 401
    assert client.get("/api/v1/ml/models").status_code == 401
