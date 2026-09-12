"""Staff API for the ML assistance features.

Behind the staff key like the rest of the real-data surface: incident
clusters list ticket references and model monitoring describes real traffic.
Nothing here writes to a ticket.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbDep
from app.api.security import StaffAuth
from app.ml.feedback import feedback_summary, recent_feedback
from app.ml.incidents import IncidentReport, detect_incidents
from app.ml.monitoring import model_registry, monitoring_snapshot
from app.schemas.ml import FeedbackOut

router = APIRouter(dependencies=[StaffAuth])

# Incident detection clusters every recent complaint. Cheap at this scale, but
# a dashboard left open on several screens should not redo it on every
# refresh, so a result is reused for a short while.
_INCIDENT_CACHE_SECONDS = 60
_incident_cache: dict[tuple[int, int], tuple[float, IncidentReport]] = {}


@router.get("/incidents")
def incidents(
    db: DbDep,
    window_hours: Annotated[int, Query(ge=1, le=72)] = 6,
    baseline_days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> dict:
    """Possible emerging incidents: unusual bursts of similar complaints."""
    key = (window_hours, baseline_days)
    cached = _incident_cache.get(key)
    if cached and time.monotonic() - cached[0] < _INCIDENT_CACHE_SECONDS:
        report = cached[1]
    else:
        report = detect_incidents(db, window_hours=window_hours, baseline_days=baseline_days)
        db.commit()  # embeddings computed on the way are derived data worth keeping
        _incident_cache[key] = (time.monotonic(), report)
    return asdict(report)


def clear_incident_cache() -> None:
    _incident_cache.clear()


@router.get("/models")
def models() -> dict:
    """Which model and policy versions this instance is running."""
    return model_registry()


@router.get("/monitoring")
def monitoring(
    db: DbDep,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    include_demo: bool = False,
) -> dict:
    """Live model behaviour: volumes, abstention, agreement, latency, drift."""
    return monitoring_snapshot(db, days=days, include_demo=include_demo)


@router.get("/feedback")
def feedback(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_demo: bool = False,
) -> dict:
    """Staff corrections recorded as training feedback, with a summary.

    Demo corrections are excluded unless asked for: anyone can create demo
    tickets, so they are not trustworthy training data.
    """
    return {
        "summary": feedback_summary(db, include_demo=include_demo),
        "items": [
            FeedbackOut.model_validate(item).model_dump(mode="json")
            for item in recent_feedback(db, limit=limit, include_demo=include_demo)
        ],
    }
