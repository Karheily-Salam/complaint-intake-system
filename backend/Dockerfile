# Backend image: FastAPI + Alembic, served by uvicorn.
#
# The backend package expects its "backend directory" (the anchor for
# backend/.env, alembic.ini and the SQLite path - see app/core/config.py's
# BACKEND_DIR = Path(__file__).resolve().parents[2]) to be the working
# directory. Placing the backend/ contents at /app makes /app that anchor.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# pyproject.toml is the single source of dependency truth (no lockfile).
# Editable install: `import app` always resolves to /app/app, so BACKEND_DIR
# (Path(__file__).resolve().parents[2]) is /app and the YAML schema / Jinja
# prompt directories, alembic.ini and the SQLite path all resolve correctly -
# a non-editable install could shadow them from site-packages.
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# Operator scripts that are meant to be run inside the container:
#   check_email.py - mailbox pre-flight, before switching to a real mailbox
#   seed_demo.py   - populate/reset the demo dataset (demo-scoped rows only)
#   download_embedding_model.py   - fetch the optional multilingual ONNX model
#   export_training_feedback.py   - export staff corrections, masked, for review
# Copied file-by-file rather than the whole scripts/ directory, to keep
# scripts/reset_db.py - which drops the entire database - out of production.
COPY scripts/check_email.py ./scripts/check_email.py
COPY scripts/seed_demo.py ./scripts/seed_demo.py
COPY scripts/download_embedding_model.py ./scripts/download_embedding_model.py
COPY scripts/export_training_feedback.py ./scripts/export_training_feedback.py
RUN pip install -e .

# Run as a non-root user. /app/data is where the persistent SQLite database
# lives (DATABASE_URL=sqlite:////app/data/complaint_intake.db) and is backed
# by a named Docker volume; creating + chowning it here means a fresh volume
# inherits appuser ownership so startup migrations can write to it.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
