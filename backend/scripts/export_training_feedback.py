"""Export staff corrections as a privacy-masked training dataset (JSONL).

    cd backend
    python scripts/export_training_feedback.py --out datasets/feedback/feedback.jsonl
    python scripts/export_training_feedback.py --mark-exported   # record what was exported

Each line is one correction: the model input (the inbound message the
original value came from, quoted history removed, identifiers masked), what
the system had, what staff corrected it to, and which layer / model version
was wrong. That is exactly what a future retraining or error analysis needs,
and nothing more.

Privacy and scope, by default:

* identifiers in text and values are masked (app.ml.pii.mask_identifiers);
* demo corrections are excluded - anyone can create demo tickets;
* tickets listed in ML_DATASET_EXCLUDED_TICKETS (and any --exclude-ticket)
  are skipped entirely - test artefacts must never become training data;
* nothing is retrained. The output is a file for a human to review.

The output directory backend/datasets/feedback/ is git-ignored: even masked,
data derived from real customer mail does not belong in the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.db.base import Base  # noqa: E402,F401 - registers every mapper
from app.db.models.message import Message  # noqa: E402
from app.db.models.ml_feedback import MLFeedback  # noqa: E402
from app.db.models.ticket import Ticket  # noqa: E402
from app.email.quoting import strip_quoted_reply  # noqa: E402
from app.ml.pii import mask_identifiers  # noqa: E402


def build_records(db, *, include_demo: bool, excluded: set[str], include_exported: bool):
    stmt = select(MLFeedback, Ticket.reference).join(Ticket, Ticket.id == MLFeedback.ticket_id)
    if not include_demo:
        stmt = stmt.where(MLFeedback.is_demo.is_(False))
    if not include_exported:
        stmt = stmt.where(MLFeedback.exported.is_(False))
    rows = []
    for feedback, reference in db.execute(stmt.order_by(MLFeedback.id)).all():
        if reference in excluded:
            continue
        text = None
        if feedback.source_message_id:
            message = db.get(Message, feedback.source_message_id)
            if message is not None:
                text = mask_identifiers(strip_quoted_reply(message.body))
        rows.append(
            (
                feedback,
                {
                    "id": f"fb-{feedback.id}",
                    "kind": feedback.kind,
                    "language": feedback.language_code,
                    "field_key": feedback.field_key,
                    "text": text,
                    "original_value": mask_identifiers(feedback.original_value or "") or None,
                    "corrected_value": mask_identifiers(feedback.corrected_value),
                    "original_source": feedback.original_source,
                    "original_confidence": feedback.original_confidence,
                    "model_version": feedback.model_version,
                    "created_at": feedback.created_at.isoformat(),
                },
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", default=str(BACKEND_DIR / "datasets" / "feedback" / "feedback.jsonl")
    )
    parser.add_argument("--include-demo", action="store_true", help="also export demo corrections")
    parser.add_argument("--exclude-ticket", action="append", default=[], help="ticket reference")
    parser.add_argument("--include-exported", action="store_true", help="re-export everything")
    parser.add_argument(
        "--mark-exported", action="store_true", help="flag the exported rows as exported"
    )
    args = parser.parse_args()

    excluded = set(settings.ml_dataset_excluded_tickets) | set(args.exclude_ticket)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        rows = build_records(
            db,
            include_demo=args.include_demo,
            excluded=excluded,
            include_exported=args.include_exported,
        )
        with out.open("w", encoding="utf-8", newline="\n") as fh:
            for _, record in rows:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if args.mark_exported:
            for feedback, _ in rows:
                feedback.exported = True
            db.commit()
    print(f"exported {len(rows)} correction(s) to {out}")
    if excluded:
        print(f"excluded tickets: {', '.join(sorted(excluded))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
