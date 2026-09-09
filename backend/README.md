# Backend

FastAPI + Pydantic + SQLAlchemy 2.0 + SQLite + Alembic.

## Layout

| Path | Responsibility |
|---|---|
| `app/core/` | config (env-driven), DB engine/session, logging, Alembic runner (`migrations.py`), sqlite path resolution (`db_path.py`) |
| `app/db/models/` | SQLAlchemy ORM models |
| `app/repositories/` | data-access layer (no business logic) |
| `app/schemas/` | Pydantic request/response DTOs |
| `app/domain/enums.py` | shared domain enums |
| `app/domain/complaint_schemas/` | **business config**: field schemas as YAML + registry |
| `app/domain/validation.py` | schema-driven field validation |
| `app/conversation/engine.py` | the deterministic multi-turn Conversation Engine (pure, no I/O) |
| `app/conversation/state.py` | `ConversationState` / `EngineOutcome` value objects |
| `app/conversation/prompts/` | Jinja prompt templates (presentation only) |
| `app/ai/` | `AIProvider` abstraction + `rule_based` / `ollama` providers + factory |
| `app/email/` | `EmailProvider` abstraction + `MockEmailProvider` + factory |
| `app/services/` | orchestration: `IntakeService`, `TicketService`, `ConversationService` |
| `app/api/routes/` | HTTP endpoints |
| `alembic/` | migrations |
| `tests/` | pytest (uses the rule-based provider, in-memory-ish SQLite) |

## Key flows

- `POST /api/v1/inbox` — simulate a customer email. `IntakeService` builds a
  `ConversationState` from the DB, calls `ConversationEngine.advance()`, persists
  the resolved fields, sends a reply via `MockEmailProvider`, and creates (or
  refreshes) a ticket once the deterministic required-field set is satisfied.
- `GET /api/v1/tickets`, `GET /api/v1/tickets/{ref}`, `PATCH /api/v1/tickets/{ref}`
  — employee dashboard.
- `GET /api/v1/schemas` — complaint field schemas for the frontend.

## Conversation engine guarantees

The engine (not the AI provider) owns all state. It handles: information arriving
in any order and across many messages; never re-asking a collected valid field;
merging new extractions without clobbering existing values on empty extraction;
detecting invalid values and asking again; customer corrections (invalid→valid
and valid→valid); the complaint type becoming known only after an ambiguous first
message; and switching to ticketed exactly when the schema's required fields are
all valid. The engine **never branches on a field value** — it only asks the
schema "what fields are required?" and collects them. See
`tests/test_conversation_flows.py` and `tests/test_engine_merge.py`.

## AI providers

`AIProvider` (`app/ai/base.py`) is the only AI surface the engine sees. Methods:
`classify`, `extract`, `summarize`, `compose_reply`, `available`. Selected by
`AI_PROVIDER`:

- `rule_based` (default) — offline, deterministic, no network. The test baseline.
- `ollama` — local LLM via `http://localhost:11434`. Typed I/O, schema-derived
  field definitions, instructed never to invent values. If Ollama is unreachable
  it falls back to `rule_based` (`OLLAMA_FALLBACK_TO_RULE_BASED=true`, default) or
  raises `AIProviderError`.

## Adding / changing a complaint type

Edit or add a YAML file in `app/domain/complaint_schemas/definitions/`. No engine,
service, or prompt changes required — the engine reads required fields from the
schema each turn.

**Deposit method is a generic free-text field.** Available methods differ by
country/market and change over time, so the prototype does not model specific
methods (no `bank_transfer` / `crypto` / `card` / `e_wallet` anywhere). The AI
extracts whatever the customer says (e.g. "my local payment wallet") verbatim;
it is never mapped to a category and never triggers extra required fields.
`ComplaintSchema.fields_for()` is the seam where method/country-specific rules
could be layered in later without touching the engine — see
`definitions/deposit_methods/README.md`.

## Regenerating a migration after model changes

```bash
../.venv/Scripts/python.exe -m alembic revision --autogenerate -m "describe change"
../.venv/Scripts/python.exe -m alembic upgrade head
```

## Database initialization

Alembic is the only schema-authoring mechanism - there is no
`Base.metadata.create_all()` or hand-written DDL anywhere in `app/`.

- **App startup**: `app/main.py`'s `lifespan` calls `app.core.migrations.run_migrations()`
  (a thin wrapper around `alembic.command.upgrade(cfg, "head")`) whenever
  `settings.run_migrations_on_startup` is true (the default for this
  prototype). This is what makes a fresh clone "just work" - no manual
  `alembic upgrade head` required before the first `uvicorn` run.
- **Explicit migrations**: `../.venv/Scripts/python.exe -m alembic upgrade head`
  still works as always, and is what a real deployment should run as its
  release step, with `RUN_MIGRATIONS_ON_STARTUP=false` in that environment so
  the running process never tries to migrate the schema itself.
- **Reset for a clean prototype DB**: `../.venv/Scripts/python.exe -m scripts.reset_db`
  deletes the current SQLite file (if any) and re-applies every migration
  from scratch via Alembic.
- **Path resolution**: `DATABASE_URL` may be given as a relative
  `sqlite:///./complaint_intake.db`; `app.core.db_path.anchor_sqlite_url`
  rewrites it to an absolute path under `backend/` the moment settings are
  loaded, so the app, Alembic (`alembic/env.py` reads the same `settings`),
  tests, and `scripts.reset_db` are guaranteed to use the identical physical
  file no matter which directory a command is run from. Already-absolute
  URLs (including Postgres URLs in production) are left untouched.

See `tests/test_fresh_database_startup.py` for the regression test covering
this end to end (fresh SQLite file, real app startup, `POST /api/v1/inbox`
through to a created ticket).
