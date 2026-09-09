# Backend

FastAPI + Pydantic + SQLAlchemy 2.0 + SQLite + Alembic.

## Layout

| Path | Responsibility |
|---|---|
| `app/core/` | config (env-driven), DB engine/session, logging |
| `app/db/models/` | SQLAlchemy ORM models |
| `app/repositories/` | data-access layer (no business logic) |
| `app/schemas/` | Pydantic request/response DTOs |
| `app/domain/enums.py` | shared domain enums |
| `app/domain/complaint_schemas/` | **business config**: field schemas as YAML + registry |
| `app/domain/validation.py` | schema-driven field validation |
| `app/conversation/engine.py` | the AI Conversation Engine (pure, no I/O) |
| `app/conversation/prompts/` | Jinja prompt templates (presentation only) |
| `app/ai/` | `AIProvider` abstraction + `rule_based` / `ollama` providers + factory |
| `app/email/` | `EmailProvider` abstraction + `MockEmailProvider` + factory |
| `app/services/` | orchestration: `IntakeService`, `TicketService`, `ConversationService` |
| `app/api/routes/` | HTTP endpoints |
| `alembic/` | migrations |
| `tests/` | pytest (uses the rule-based provider, in-memory-ish SQLite) |

## Key flows

- `POST /api/v1/inbox` — simulate a customer email. `IntakeService` runs the
  engine, persists extracted fields, sends a reply via `MockEmailProvider`, and
  creates a ticket once all required fields validate.
- `GET /api/v1/tickets`, `GET /api/v1/tickets/{ref}`, `PATCH /api/v1/tickets/{ref}`
  — employee dashboard.
- `GET /api/v1/schemas` — complaint field schemas for the frontend.

## Adding a complaint type or deposit method

Edit / add a YAML file in `app/domain/complaint_schemas/definitions/`. No engine,
service, or prompt changes required. See `definitions/deposit_methods/README.md`.

## Regenerating a migration after model changes

```bash
../.venv/Scripts/python.exe -m alembic revision --autogenerate -m "describe change"
../.venv/Scripts/python.exe -m alembic upgrade head
```
