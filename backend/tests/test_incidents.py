"""Emerging-incident detection.

Histories are written straight into the database with controlled timestamps,
because what is under test is the statistics: a quiet topic that suddenly
bursts is an incident, a topic that is always busy is not, and neither one
changes a single ticket.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select

from app.api.routes.ml import clear_incident_cache
from app.db.models.complaint import Complaint
from app.db.models.conversation import Conversation
from app.db.models.customer import Customer
from app.ml.incidents import (
    average_linkage_clusters,
    detect_incidents,
    distinctive_terms,
    poisson_sf,
)

NOW = datetime(2026, 9, 12, 12, 0, 0)

BURST = "The Payeer withdrawal failed with error 502 and the money left my balance"
BACKGROUND = [
    "I cannot log in because the password reset email never arrives",
    "The mobile app crashes whenever I open the settings screen",
    "My identity verification documents were rejected without a reason",
    "The promo code from the newsletter says it is not valid",
    "Bonus points from the referral programme are missing from my profile",
    "Two factor authentication codes are not delivered by SMS",
    "The trading charts do not load on the web version",
    # Everyday complaint vocabulary, so that "failed", "error" and "money"
    # are common in the history - as they are in any real inbox.
    "The card deposit failed with an error and the money is not on my balance",
    "Money left my bank account but my balance did not change",
    "The transfer failed twice with an error message",
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_incident_cache()
    yield
    clear_incident_cache()


def add_complaint(db, text, created_at, *, complaint_type="other", is_demo=True, language="en"):
    customer = Customer(email=f"{uuid.uuid4().hex[:10]}@example.com", is_demo=is_demo)
    db.add(customer)
    db.flush()
    conversation = Conversation(
        customer_id=customer.id,
        is_demo=is_demo,
        thread_token=uuid.uuid4().hex[:16],
        language_code=language,
    )
    db.add(conversation)
    db.flush()
    complaint = Complaint(
        conversation_id=conversation.id,
        type=complaint_type,
        concise_description=text,
        status="collecting",
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(complaint)
    db.flush()
    return complaint


def seed_background(db, per_topic_days=(1, 3, 5, 8, 11)):
    for text in BACKGROUND:
        for days in per_topic_days:
            add_complaint(db, text, NOW - timedelta(days=days, hours=1))


# ------------------------------------------------------------------ statistics


def test_poisson_tail_probabilities():
    assert poisson_sf(0, 2.0) == 1.0
    assert poisson_sf(1, 1.0) == pytest.approx(1 - math.exp(-1))
    assert poisson_sf(2, 1.0) == pytest.approx(1 - 2 * math.exp(-1))
    assert poisson_sf(10, 0.5) < 1e-8


def test_average_linkage_recovers_separated_groups():
    rng = np.random.default_rng(0)
    centres = np.eye(3, 16)
    points = np.vstack([c + 0.05 * rng.normal(size=(4, 16)) for c in centres])
    points /= np.linalg.norm(points, axis=1, keepdims=True)
    clusters = average_linkage_clusters(points, threshold=0.8)
    assert sorted(len(c) for c in clusters) == [4, 4, 4]
    assert {frozenset(c) for c in clusters} == {
        frozenset(range(0, 4)), frozenset(range(4, 8)), frozenset(range(8, 12))
    }
    assert len(average_linkage_clusters(points, threshold=0.9999)) == 12
    assert average_linkage_clusters(np.zeros((0, 16)), 0.5) == []


def test_distinctive_terms_prefer_what_is_specific_to_the_cluster():
    cluster = [BURST, BURST.replace("502", "503"), "Payeer payout failed, error 502"]
    everything = cluster + BACKGROUND
    terms = distinctive_terms(cluster, everything)
    assert terms[0] == "payeer"
    assert "the" not in terms and "and" not in terms
    assert "email" not in distinctive_terms(["email email email [email]"], everything)


# ------------------------------------------------------------------ detection


def test_a_sudden_burst_of_one_topic_is_an_incident(db_session):
    seed_background(db_session)
    for minutes in (10, 25, 40, 70, 95):
        add_complaint(
            db_session, BURST, NOW - timedelta(minutes=minutes), complaint_type="withdrawal",
            language="ru" if minutes % 2 else "en",
        )
    db_session.commit()

    report = detect_incidents(db_session, now=NOW)

    assert len(report.incidents) == 1
    incident = report.incidents[0]
    assert incident.size == 5 and incident.is_demo is True
    assert incident.p_value < 0.01 and incident.ratio >= 3
    assert "payeer" in incident.top_terms
    assert incident.types == {"withdrawal": 5}
    assert incident.languages == {"en": 3, "ru": 2}
    assert sum(incident.hourly_counts) == 5 and len(incident.hourly_counts) == 6
    assert incident.open_complaints == 5, "none of them has become a ticket yet"


def test_a_topic_that_is_always_busy_is_not_an_incident(db_session):
    # About one similar complaint an hour for two weeks: five in six hours is
    # below the usual rate, however alarming it would look on its own.
    for hour in range(7, 14 * 24):
        add_complaint(db_session, BURST, NOW - timedelta(hours=hour))
    for minutes in (10, 30, 50, 70, 90):
        add_complaint(db_session, BURST, NOW - timedelta(minutes=minutes))
    db_session.commit()

    assert detect_incidents(db_session, now=NOW).incidents == []


def test_scattered_unrelated_complaints_are_not_an_incident(db_session):
    seed_background(db_session)
    for minutes, text in zip((10, 40, 80, 120), BACKGROUND, strict=False):
        add_complaint(db_session, text, NOW - timedelta(minutes=minutes))
    db_session.commit()

    assert detect_incidents(db_session, now=NOW).incidents == []


def test_a_type_volume_spike_is_reported(db_session):
    seed_background(db_session)
    for i in range(6):
        add_complaint(
            db_session, BACKGROUND[i % len(BACKGROUND)] + f" case {i}",
            NOW - timedelta(minutes=10 + 15 * i), complaint_type="deposit",
        )
    db_session.commit()

    spikes = detect_incidents(db_session, now=NOW).volume_spikes
    deposit = next(s for s in spikes if s.complaint_type == "deposit")
    assert deposit.count == 6 and deposit.p_value < 0.01


def test_demo_and_real_complaints_are_analysed_separately(db_session):
    for minutes in (10, 20, 30, 40):
        add_complaint(db_session, BURST, NOW - timedelta(minutes=minutes), is_demo=True)
    for minutes in (15, 25):
        add_complaint(db_session, BURST, NOW - timedelta(minutes=minutes), is_demo=False)
    db_session.commit()

    report = detect_incidents(db_session, now=NOW)
    assert [(i.is_demo, i.size) for i in report.incidents] == [(True, 4)], (
        "four demo + two real would be six together - they must not be pooled"
    )


def test_three_complaints_with_no_history_are_not_yet_significant(db_session):
    # P(X >= 3 | 0.5) is about 0.014: suggestive, not below alpha = 0.01.
    for minutes in (10, 20, 30):
        add_complaint(db_session, BURST, NOW - timedelta(minutes=minutes))
    db_session.commit()
    assert detect_incidents(db_session, now=NOW).incidents == []


def test_detection_changes_no_complaint(db_session):
    seed_background(db_session)
    for minutes in (10, 25, 40):
        add_complaint(db_session, BURST, NOW - timedelta(minutes=minutes))
    db_session.commit()
    before = [(c.id, c.type, c.status) for c in db_session.scalars(select(Complaint))]

    detect_incidents(db_session, now=NOW)

    db_session.expire_all()
    assert [(c.id, c.type, c.status) for c in db_session.scalars(select(Complaint))] == before


# ------------------------------------------------------------------ API


def test_incidents_endpoint(staff_client, db_session):
    now = datetime.now(UTC).replace(tzinfo=None)
    for minutes in (5, 15, 25, 35):
        add_complaint(db_session, BURST, now - timedelta(minutes=minutes))
    db_session.commit()

    response = staff_client.get("/api/v1/ml/incidents?window_hours=6&baseline_days=14")
    assert response.status_code == 200
    body = response.json()
    assert body["model_version"].startswith("hashing-char-ngram")
    assert body["complaints_in_window"] == 4
    assert body["incidents"][0]["size"] == 4


def test_incidents_endpoint_is_staff_only(client):
    assert client.get("/api/v1/ml/incidents").status_code == 401


def test_incidents_endpoint_validates_its_window(staff_client):
    assert staff_client.get("/api/v1/ml/incidents?window_hours=0").status_code == 422
