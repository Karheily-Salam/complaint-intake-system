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
message; the deposit method being discovered mid-conversation, with
method-specific fields becoming required only once the method is known; and
switching to ticketed exactly when the schema requirements are met. See
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

## Adding a complaint type or deposit method

Edit / add a YAML file in `app/domain/complaint_schemas/definitions/`. No engine,
service, or prompt changes required. The prototype ships four deposit methods
(`bank_transfer`, `crypto`, `card`, `e_wallet`); see
`definitions/deposit_methods/README.md`.

## Regenerating a migration after model changes

```bash
../.venv/Scripts/python.exe -m alembic revision --autogenerate -m "describe change"
../.venv/Scripts/python.exe -m alembic upgrade head
```
