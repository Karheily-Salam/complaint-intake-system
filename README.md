# AI-Powered Email Complaint Intake & Ticketing System — Prototype

A local prototype that receives customer complaints through an email-like
interface, uses an AI Conversation Engine to classify the complaint, extract the
information already provided, ask only for what is missing, validate the result,
and generate a structured ticket for an employee dashboard.

> Prototype constraints: runs fully locally, no paid APIs, no connection to any
> real company email account. The AI and email layers are abstractions so a local
> LLM (Ollama) and real mail providers (Gmail, Microsoft Graph) can be added later
> without touching the conversation engine.

## Architecture at a glance

```
Customer's mailbox ──email──►  EmailPoller ──►  IntakeService (orchestration)
Frontend (React/TS)  ──REST──►  FastAPI   ──►      │
  • Mailbox Simulator (dev only)                   ├─► ConversationEngine (pure domain logic)
  • Employee Dashboard                             │      depends on ▼        ▼
                                                   │        AIProvider    ComplaintSchemaRegistry
                                                   │        ├ RuleBasedAIProvider (default, offline)
                                                   │        └ OllamaAIProvider (local LLM, opt-in)
                                                   ├─► EmailProvider
                                                   │     ├ MockEmailProvider (dev/tests)
                                                   │     └ ImapSmtpEmailProvider (IMAP in, SMTP out)
                                                   └─► Repositories ── SQLAlchemy ── SQLite
```

The customer never sees a web page: they email the complaints mailbox and get
replies in the same thread. The React app is for staff (the dashboard) and for
local development (the mailbox simulator).

Business rules (required fields, validation) live in **YAML schema files** under
`backend/app/domain/complaint_schemas/definitions/`, never in prompts or engine
code. The engine never branches on a field value — the deposit method is a
generic free-text field, not a fixed list.

See `docs/` — *(to be added)* — for deeper notes; `backend/README.md` for backend
specifics.

## Prerequisites

- Python 3.12+ (repo currently uses 3.14) with the virtualenv in `.venv/`
- Node.js 20+ / npm 10+

## Run the backend

```bash
cd backend
# 1. install deps into the existing .venv
../.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# ../.venv/bin/python -m pip install -e ".[dev]"           # macOS/Linux

# 2. configure
cp .env.example .env

# 3. start the API - pending Alembic migrations are applied automatically on
#    startup for this local prototype (RUN_MIGRATIONS_ON_STARTUP=true), so a
#    fresh clone needs no separate init step.
../.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

API docs at http://localhost:8000/docs — health at
http://localhost:8000/api/v1/health.

The SQLite file path (`DATABASE_URL` in `backend/.env`) is always resolved
relative to `backend/`, never to whatever directory you happen to run the
command from — so `alembic`, `uvicorn`, `pytest`, and `scripts.reset_db` all
share the exact same database file regardless of your current shell location.

If you'd rather not rely on automatic startup migrations (e.g. to mirror a
production deploy step, or after pulling new migrations while the app isn't
running), run them explicitly instead:

```bash
../.venv/Scripts/python.exe -m alembic upgrade head
```

To wipe the local database and rebuild it from the migrations from scratch:

```bash
../.venv/Scripts/python.exe -m scripts.reset_db
```

For a real deployment, set `RUN_MIGRATIONS_ON_STARTUP=false` and run
`alembic upgrade head` as an explicit release step instead of on every
process start.

## Run the frontend

```bash
cd frontend
npm install
cp .env.example .env.local   # optional
npm run dev                  # http://localhost:5173  (proxies /api to :8000)
```

## Tests

```bash
cd backend
../.venv/Scripts/python.exe -m pytest   # schema, multi-turn flows, engine merge, provider fallback
```

## Email architecture

`EMAIL_PROVIDER` selects the transport; nothing else in the system changes with
it. The conversation engine has no idea email exists.

| | `mock` (default) | `imap_smtp` |
|---|---|---|
| Inbound | `POST /api/v1/inbox` (dev simulator) | background poller, IMAP `UNSEEN` search every `EMAIL_POLL_INTERVAL_SECONDS` |
| Outbound | captured in memory | SMTP (STARTTLS or implicit TLS) |
| Credentials | none | dedicated mailbox + app password, env vars only |

**Why IMAP/SMTP.** It works with any mailbox provider using an app password -
no OAuth app registration (Gmail API / Microsoft Graph), no domain or MX
records to point at an inbound-webhook service (Mailgun/SendGrid/Postmark),
and no inbound port on the server, since polling is an outbound connection.
It is also the easiest to replace later: a webhook or Graph provider only has
to implement `fetch_new`/`send`/`mark_processed`.

**Threading.** Every outgoing message stores the `Message-ID` we sent
(`messages.external_message_id`). A customer reply is matched by its
`In-Reply-To`, then its `References` chain; if a provider rewrote those
headers in transit, the opaque `[Ref:…]` token in the subject line is the
fallback. The sender's address is deliberately never used on its own, because
one customer can have several complaints open at once.

**Idempotency.** The inbound `Message-ID` is stored under a unique index, so a
redelivered email cannot create a second conversation, question, or ticket. A
message is acknowledged with the provider (`\Seen`) only *after* its
transaction commits, so a crash mid-turn means a clean retry rather than a
lost complaint.

**Failure handling.** The whole turn - persistence, reply, ticket notification
- is one transaction. If the provider fails to send, it rolls back and the
email is retried on the next poll, so the conversation is never left advanced
with the customer never asked.

To go live, see "Going live with real email" in [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Using a local LLM later

Install [Ollama](https://ollama.com), `ollama pull llama3.1`, then set in
`backend/.env`:

```
AI_PROVIDER=ollama
OLLAMA_MODEL=llama3.1
```

No other code changes required. If Ollama is not reachable the provider falls
back to `rule_based` (set `OLLAMA_FALLBACK_TO_RULE_BASED=false` to get a hard
error instead). `GET /api/v1/health` reports `ai_provider_available`.
