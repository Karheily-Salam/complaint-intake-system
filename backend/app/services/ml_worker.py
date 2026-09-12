"""Background ML work, kept off the email and request paths.

Embedding a complaint costs tens of milliseconds with the ONNX model - fine
in the background, but not something to add between a customer's email and
the reply. So intake never embeds: this loop picks up new and changed
complaints every ``ML_WORKER_INTERVAL_SECONDS`` and embeds them in a worker
thread with its own database session, leaving the event loop (and with it the
mail poller and the API) free.

Nothing here can affect a ticket. It only writes derived data - embeddings -
and a failure is logged and retried on the next cycle.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.ml.embedding_store import embed_pending
from app.ml.embeddings import get_embedder

logger = get_logger(__name__)


@dataclass
class MLWorkerHealth:
    last_run_at: datetime | None = None
    last_duration_ms: float | None = None
    last_embedded: int = 0
    total_embedded: int = 0
    runs: int = 0
    failures: int = 0
    last_failure_at: datetime | None = None

    def snapshot(self) -> dict:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "last_run_at": iso(self.last_run_at),
            "last_duration_ms": self.last_duration_ms,
            "last_embedded": self.last_embedded,
            "total_embedded": self.total_embedded,
            "runs": self.runs,
            "failures": self.failures,
            "last_failure_at": iso(self.last_failure_at),
        }


ml_worker_health = MLWorkerHealth()


def run_once(session_factory=SessionLocal, *, batch: int = 64) -> int:
    """One pass: embed whatever is missing or stale. Returns vectors written."""
    started = time.perf_counter()
    embedder = get_embedder()
    written = 0
    with session_factory() as db:
        while True:
            count = embed_pending(db, embedder, limit=batch)
            db.commit()
            written += count
            if count < batch:
                break
    ml_worker_health.runs += 1
    ml_worker_health.last_run_at = datetime.now(UTC)
    ml_worker_health.last_embedded = written
    ml_worker_health.total_embedded += written
    ml_worker_health.last_duration_ms = round((time.perf_counter() - started) * 1000, 1)
    if written:
        logger.info("ml_worker embedded=%d model=%s", written, embedder.version)
    return written


async def run_forever() -> None:
    interval = max(5, settings.ml_worker_interval_seconds)
    logger.info("ML worker started (every %ss, embedder=%s)", interval, get_embedder().version)
    while True:
        try:
            # Sleep first: startup belongs to migrations and the mail poller,
            # and a short-lived process (a test, a one-off command) exits
            # before any background thread has touched its database.
            await asyncio.sleep(interval)
            await asyncio.to_thread(run_once)
        except asyncio.CancelledError:
            logger.info("ML worker stopped")
            raise
        except Exception:
            ml_worker_health.failures += 1
            ml_worker_health.last_failure_at = datetime.now(UTC)
            logger.exception("ML worker cycle failed; retrying next interval")
