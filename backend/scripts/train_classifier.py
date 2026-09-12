"""Train the complaint-type classifier and write its artifact.

    cd backend
    python scripts/train_classifier.py

Reads ``datasets/complaints/train.jsonl`` only - the test set is never
touched here - and writes ``app/ml/artifacts/complaint_classifier.{npz,json}``.
Deterministic: rerunning on unchanged data reproduces the same weights and
the same version string.

This never runs in production. A retrained model reaches production only by
being committed, reviewed and deployed like any other code change.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.ml.classifier import DEFAULT_ARTIFACT  # noqa: E402
from app.ml.training import file_sha256, load_examples, train_classifier  # noqa: E402

TRAIN_FILE = BACKEND_DIR / "datasets" / "complaints" / "train.jsonl"


def main() -> None:
    examples = load_examples(TRAIN_FILE)
    model, report = train_classifier(examples, dataset_sha256=file_sha256(TRAIN_FILE))
    model.save(DEFAULT_ARTIFACT)
    print(f"model      {model.version}")
    print(f"examples   {len(examples)}")
    print(f"l2         {report.l2}")
    print(f"cv         {report.cv}")
    print(f"temperature {report.temperature:.3f}")
    print(f"threshold  {report.threshold:.2f}")
    print(f"written    {DEFAULT_ARTIFACT.with_suffix('.npz').relative_to(BACKEND_DIR)}")


if __name__ == "__main__":
    main()
