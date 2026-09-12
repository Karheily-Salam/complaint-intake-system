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
| `app/ai/` | `AIProvider` abstraction + `rule_based` / `ollama` / `hybrid` providers + factory |
| `app/ml/` | classifier, features, embeddings, similarity, incidents, feedback, evaluation, monitoring, PII masking |
| `app/domain/evidence.py` | proves every extracted value against the customer's own words |
| `datasets/` | hand-written EN/RU/AR datasets (training + held-out test) |
| `app/email/` | `EmailProvider` abstraction + `MockEmailProvider` + factory |
| `app/services/` | orchestration: `IntakeService`, `TicketService`, `ConversationService` |
| `app/api/routes/` | HTTP endpoints |
| `alembic/` | migrations |
| `tests/` | pytest (uses the rule-based provider, in-memory-ish SQLite) |

## Key flows

- `POST /api/v1/inbox`: simulate a customer email. `IntakeService` builds a
  `ConversationState` from the DB, calls `ConversationEngine.advance()`, persists
  the resolved fields, sends a reply via `MockEmailProvider`, and creates (or
  refreshes) a ticket once the deterministic required-field set is satisfied.
- `GET /api/v1/tickets`, `GET /api/v1/tickets/{ref}`, `PATCH /api/v1/tickets/{ref}`:
  the employee dashboard.
- `GET /api/v1/schemas`: complaint field schemas for the frontend.

## Conversation engine guarantees

The engine (not the AI provider) owns all state. It handles: information arriving
in any order and across many messages; never re-asking a collected valid field;
merging new extractions without clobbering existing values on empty extraction;
detecting invalid values and asking again; customer corrections (invalid→valid
and valid→valid); the complaint type becoming known only after an ambiguous first
message; and switching to ticketed exactly when the schema's required fields are
all valid. The engine **never branches on a field value**; it only asks the
schema "what fields are required?" and collects them. See
`tests/test_conversation_flows.py` and `tests/test_engine_merge.py`.

## AI providers

`AIProvider` (`app/ai/base.py`) is the only AI surface the engine sees. Methods:
`classify`, `extract`, `summarize`, `compose_reply`, `available`. Selected by
`AI_PROVIDER`:

- `rule_based` (default): offline, deterministic, no network. The test baseline.
- `ollama`: local LLM via `http://localhost:11434`. Typed I/O, schema-derived
  field definitions, instructed never to invent values. If Ollama is unreachable
  it falls back to `rule_based` (`OLLAMA_FALLBACK_TO_RULE_BASED=true`, default) or
  raises `AIProviderError`.

Whichever is selected is then wrapped by `HybridAIProvider` unless
`ML_CLASSIFIER_MODE=off`: keyword rules decide first, the calibrated classifier
only fills in what they miss, and everything else is delegated untouched. See
[docs/ml.md](../docs/ml.md).

### ML scripts

| Command | What it does |
|---|---|
| `python scripts/train_classifier.py` | retrains the complaint-type classifier from `datasets/complaints/train.jsonl` and rewrites its artifact |
| `python scripts/evaluate_ml.py` | runs the held-out sets through the real code paths and regenerates `docs/ml/evaluation.{md,json}` |
| `python scripts/download_embedding_model.py` | fetches the optional multilingual ONNX encoder (pinned by SHA-256) |
| `python scripts/export_training_feedback.py` | exports staff corrections as a masked JSONL dataset for review |

A test fails if `datasets/complaints/train.jsonl` changes without the model
being retrained, or if the committed evaluation report goes stale.

## Language

Every customer-facing reply is written in the language of the customer's
*latest* message, not always English:

- `AIProvider.detect_language()` (implemented by both `RuleBasedAIProvider`
  and `OllamaAIProvider`) detects/infers the language of the inbound
  message. The engine, not the provider, decides what to do with that:
  `ConversationEngine._resolve_language()` prefers the newly detected
  language, and falls back to the conversation's previously stored language
  only when the latest message has no reliable language signal (e.g. it is
  only digits) - so an ambiguous reply doesn't reset a conversation back to
  English.
- The resolved code is persisted as `Conversation.language_code` (added via
  the `add conversation language_code` migration) so it stays consistent
  across turns, and is exposed on `ConversationOut` for the frontend/API.
- `RuleBasedAIProvider` supports English/Russian/Arabic deterministically:
  script-based Unicode detection (no model, no network) for
  `detect_language`, and a small localized phrase table for
  `compose_reply`'s scaffolding (greeting, corrections intro, footer, etc.)
  plus a per-field question table (see "One field at a time" below). It does
  **not** translate schema-driven content (complaint labels, field
  labels/descriptions from the YAML schemas) - only the surrounding
  natural-language text is localized. Real translation of arbitrary business
  content needs a model; that's `OllamaAIProvider`, which receives the
  resolved language via `ReplyRequest.language_code` and is instructed
  (`prompts/reply.jinja`) to write the entire reply in it.
- Plain Latin/ASCII text is deliberately weak, slow-growing evidence for
  `detect_language`'s `"en"` result (unlike Arabic/Cyrillic, which are
  confident on any occurrence) - a short reply that is little more than a
  copied field label and a code (e.g. `"transaction id: TXN-9f3a12bc"`,
  exactly what our own field extraction requires the customer to send) must
  not out-vote an established Arabic/Russian conversation and flip the final
  ticket confirmation into English. Because English is also the engine's
  ultimate default when nothing is confident, this asymmetry never hurts a
  real English conversation - see
  `tests/test_one_field_at_a_time.py::test_ticket_confirmation_stays_in_arabic_even_when_last_answer_is_pure_latin`.
- This only changes *how* the reply is phrased. Classification, required
  fields, completeness, and ticket creation are entirely unaffected - see
  `tests/test_multilingual_replies.py`.
- The final ticket-confirmation reply is composed *after* ticket creation,
  not inside `ConversationEngine.advance` (which performs no I/O and so
  cannot create the ticket) - see `ConversationEngine.compose_ticket_confirmation`,
  called from `IntakeService` once the real `Ticket.reference` exists, so the
  confirmation can include it instead of omitting it or an LLM inventing one.
  See `tests/test_ticket_confirmation_includes_reference.py`.

### One field at a time

The customer is asked for exactly one missing required field per message,
never a bulleted list:

- `ConversationEngine.advance` computes the full `missing` list in schema
  order (via `_classify_fields`, which walks `schema.fields_for()` in YAML
  declaration order) but only ever passes `missing[:1]` into the ASK
  `ReplyRequest` - the engine decides *which* field is next; the AI/provider
  layer decides *how* to phrase asking for it. `outcome.missing_fields` (and
  therefore the API's `IntakeResult.missing_fields`) still reports the full
  remaining set, unchanged - only the customer-facing `reply_body` is
  restricted to one field.
- `RuleBasedAIProvider._FIELD_QUESTION_PHRASES` maps each field's internal
  `key` (never renamed, never exposed to the customer) to a natural,
  standalone question per supported language, with a generic templated
  fallback for any field key not in the table. There is no
  `if language == "..."` branching in the engine - only this presentation
  table, keyed by field key and language, decides wording.
  `OllamaAIProvider`'s prompt (`prompts/reply.jinja`) is instructed to ask
  about exactly the one field the engine listed, in one natural sentence,
  never a list.
- If the customer supplies several fields in one message, extraction picks up
  all of them; the next question is simply whichever field is now
  earliest-in-schema-order among what's still missing - see
  `tests/test_one_field_at_a_time.py`.

### Pending field (contextual answers)

This is an email conversation, not a sequence of independent messages: a
bare reply like `"583921"` to "please provide your user ID" must be
interpreted as that user ID, not require the customer to repeat the field's
name, and this must work for every required field and in every supported
language.

- `ConversationEngine.advance` sets `outcome.pending_field` from
  `_classify_fields`'s `next_unresolved`: the first field in schema order
  that is not yet resolved - invalid **or** missing, whichever comes first -
  and reuses that exact same value for the ASK reply (no duplicate
  field-selection logic). `IntakeService` persists it onto
  `Conversation.pending_field` (see the `add conversation pending_field`
  migration) so the *next* inbound message's `ConversationState.pending_field`
  carries it forward. Treating invalid and missing uniformly this way is
  what makes an invalid answer to the field just asked keep the conversation
  on that same field, instead of jumping ahead to a later field that merely
  happens to be missing (`missing[0]` alone ignored the `invalid` list
  entirely - see `tests/test_pending_field_invalid_priority.py`).
- `AIProvider.extract(..., pending_field=...)` receives it. In
  `RuleBasedAIProvider`, the existing per-field extractors (labels, email,
  numeric dates, ...) always run first and are never overridden; the pending
  field is only a **fallback** for whichever one field they left unresolved,
  dispatched purely on the field's declared `type`/key shape - never on
  which specific field it is:
  - identifier-shaped fields (`user_id`, `withdrawal_transaction_id`, ...) -
    the first digit-bearing token anywhere in the message
    (`_first_value_token`) - never a bare word like "hello", so an
    unanswerable reply correctly leaves the field missing and the same
    question is asked again;
  - `DATE` fields - a small multilingual month-name parser
    (`_parse_natural_date`) for phrasing like "8 September" / "8 сентября" /
    "8 سبتمبر" with no numeric format at all;
  - free-text fields (`deposit_method`, `source_wallet_or_account`) - the
    whole message, unless it's just a greeting/acknowledgement
    (`_is_pure_filler`).
  `OllamaAIProvider` instead just tells the model which field was pending
  (`prompts/extract.jinja`) and lets it interpret the reply naturally.
- The engine still owns completeness and ticket creation entirely from the
  deterministic missing/invalid computation - `pending_field` only feeds
  extraction, exactly like `known` already does.
- See `tests/test_pending_field_context.py`.

### Customer-facing wording (corrections and the final confirmation)

Two kinds of customer-facing text are deliberately never built from raw
internal data:

- **An invalid answer** never shows the schema key or the raw validation
  error (e.g. "Not a valid email address."). `RuleBasedAIProvider` maps the
  single invalid field's key to a natural, per-language explanation of what
  was wrong and what to send instead - `_FIELD_INVALID_PHRASES`, with a
  format/example where that helps (email, date). `OllamaAIProvider`'s prompt
  is given the raw error only as context for itself and is explicitly told
  never to quote it. Both still address only the one field
  `ConversationEngine` says is pending - see "Pending field" above.
- **The final ticket confirmation** never names the internal complaint
  classification (e.g. "withdrawal problem"). `ConversationEngine.compose_ticket_confirmation`
  builds `collected_fields` from the resolved, non-free-text values in
  schema order (`FieldType.TEXT` fields like `problem_description` are
  excluded - a narrative paragraph isn't a concise fact to list); each
  provider then displays them under its own localized noun labels
  (`RuleBasedAIProvider._FIELD_DISPLAY_LABELS`, e.g. "رقم المستخدم" /
  "User ID" / "ID пользователя" - never the raw key), states the
  information was sent to the specialist team, and includes the ticket
  reference.
- See `tests/test_customer_facing_messages.py`.

### Ticket reference

The customer-facing reference is numeric-only (e.g. `"000001"`), generated
by `TicketService._make_reference` from the ticket's own auto-incrementing
primary key - already unique and collision-safe by the database itself, on
SQLite today and identically on PostgreSQL later, so there's no separate
counter to keep in sync or race on. The AI layer never generates or alters
it: `TicketService` creates the row (flushing once to get its id, then
setting the real reference), and the *same* persisted value flows into the
dashboard (`GET /api/v1/tickets`), the API response, and
`ReplyRequest.ticket_reference` for the confirmation text.

## Adding / changing a complaint type

Edit or add a YAML file in `app/domain/complaint_schemas/definitions/`. No engine,
service, or prompt changes required. The engine reads required fields from the
schema each turn.

**Deposit method is a generic free-text field.** Available methods differ by
country/market and change over time, so the prototype does not model specific
methods (no `bank_transfer` / `crypto` / `card` / `e_wallet` anywhere). The AI
extracts whatever the customer says (e.g. "my local payment wallet") verbatim;
it is never mapped to a category and never triggers extra required fields.
`ComplaintSchema.fields_for()` (in `app/domain/complaint_schemas/spec.py`) is the
seam where method/country-specific rules could be layered in later without
touching the engine: it is already asked "what fields are required right now?"
every turn, so a wrapper returning extra `FieldSpec`s needs no engine change.

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
- **`alembic/env.py`'s `fileConfig()` call passes `disable_existing_loggers=False`.**
  This matters only because migrations also run *in-process* on startup (the
  point above) - `env.py` executes there too, and `fileConfig`'s default
  disables every logger not declared in `alembic.ini`'s `[loggers]` section,
  including uvicorn's. Without this, a perfectly successful startup goes
  silent right after "Applying database migrations" (no "Application
  startup complete.", no request logs, no visibility into any later error)
  and looks hung, even though the server is usually still running - the
  plain `alembic` CLI is unaffected either way since it has no other
  process-wide logging to preserve. See
  `tests/test_migrations_logging.py` and
  `tests/test_startup_migration_subprocess.py`.

See `tests/test_fresh_database_startup.py` for the regression test covering
this end to end (fresh SQLite file, real app startup, `POST /api/v1/inbox`
through to a created ticket).
