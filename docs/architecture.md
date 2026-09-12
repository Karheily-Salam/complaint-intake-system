🇬🇧 **English** | [🇷🇺 Русский](architecture.ru.md)

# Architecture

A single reference for how the system is actually built. The
[README](../README.md) is the introduction; this is the level of detail a
developer needs before changing something. Everything here describes code that
exists. Where a capability is implemented but not switched on, it says so.

## Contents

- [System overview](#system-overview)
- [Email intake flow](#email-intake-flow)
- [Complaint classification](#complaint-classification)
- [One field at a time](#one-field-at-a-time)
- [Conversation and thread handling](#conversation-and-thread-handling)
- [Email threading](#email-threading)
- [Idempotency](#idempotency)
- [Ticket lifecycle](#ticket-lifecycle)
- [Closed-ticket behaviour](#closed-ticket-behaviour)
- [Support dashboard](#support-dashboard)
- [AI provider abstraction](#ai-provider-abstraction)
- [ML layer](#ml-layer)
- [Mock email provider](#mock-email-provider)
- [Production email architecture](#production-email-architecture)
- [Rate limiting](#rate-limiting)
- [Deployment architecture](#deployment-architecture)
- [Security model](#security-model)
- [Testing](#testing)

## System overview

Four layers, with dependencies pointing inward:

| Layer | Responsibility | Knows about |
|---|---|---|
| `EmailProvider` | transport: fetch and send mail | nothing above it |
| `IntakeService` | orchestration, persistence, transaction boundary | provider, engine, repositories |
| `ConversationEngine` | what to ask next, whether the complaint is complete | `AIProvider` interface, schema registry |
| `AIProvider` | classify, extract, summarise, phrase, detect language | nothing |

`ConversationEngine` performs no I/O, holds no state, and has no idea email
exists. That is why the same engine serves both the real mailbox and the
browser demo, and why replacing the mail transport touches no business logic.

Business rules live in YAML under
`backend/app/domain/complaint_schemas/definitions/`, loaded by a registry that
is the single source of truth for required fields, validation, labels and
display grouping. Adding a complaint type is a file, not a code change.

## Email intake flow

`EmailPoller` runs inside the API process. With IMAP IDLE the server pushes
a notification as soon as mail lands and the configured interval (~60s) is
only the fallback ceiling for a quiet mailbox; with IDLE off it is a plain
fixed-interval poll:

1. `fetch_new()`, an IMAP `UNSEEN` search. The provider does **not** mark
   anything read.
2. For each message, `IntakeService.handle_inbound_email()`:
   - returns early if this `Message-ID` was already processed;
   - resolves or creates the customer, then the conversation;
   - persists the inbound message;
   - runs one engine turn;
   - creates or refreshes the ticket if the complaint is now complete;
   - sends the reply and, for a new ticket, the support notification;
   - **commits**.
3. Only after the commit does the poller call `mark_processed()`, which is what
   sets `\Seen`.

That ordering is the durability guarantee: a crash anywhere before the commit
leaves the message unread, so the next cycle retries it cleanly. The system
can never end up with a conversation advanced but the customer never asked.
One failing message is logged and skipped without blocking the rest of the
batch.

## Complaint classification

The AI layer proposes a type and a confidence. Below the threshold the engine
asks the customer to clarify instead of guessing, because a wrong type collects
the wrong fields and produces a useless ticket. Classification is re-attempted
on later messages until it succeeds, so a vague opener does not doom the
thread.

The set of types comes from the registry, never from a hard-coded list.

## One field at a time

Given the complaint type, the engine compares the required fields from the
schema against what has been collected, and asks for exactly **one**:

- an **invalid** value outranks a merely missing one, so a correction is
  requested before moving on;
- otherwise the first missing field in schema order.

The field order in the YAML is therefore the question order, which is why
those files are never reordered casually.

A bare reply (`"583921"`) is interpreted against the field that was actually
asked, using the conversation's stored `pending_field`. Values the customer
volunteers are absorbed and not asked for again, so answering three
questions in one message skips all three.

## Conversation and thread handling

A `Conversation` owns its messages, its single `Complaint`, and at most one
`Ticket`. A customer may hold several conversations at once, which is exactly
why the sender's address is never used on its own to decide which thread an
email belongs to.

`Conversation.status` (`open` → `collecting_info` → `validating` → `completed`)
tracks intake progress and is distinct from `Ticket.status`, which tracks the
support workflow. Both exist because they answer different questions: whether
the system still needs something from the customer, and whether support has
finished with the case.

## Email threading

`IntakeService._resolve_conversation()` tries, in order:

1. `In-Reply-To` matched against the `Message-ID` of a message we sent;
2. the `References` chain, newest first;
3. the opaque `[Ref:<token>]` token in the subject, the fallback for providers
   that rewrite `Message-ID`s in transit.

Every candidate is then passed through `_is_threadable()`, which is the single
gate for all three signals. It refuses a conversation belonging to a different
customer, a demo conversation (real mail never joins synthetic data), and a
conversation whose ticket is closed. Anything refused falls through to a brand
new conversation.

A new conversation's stored subject has any inherited `[Ref:…]` token stripped,
so it advertises its own token and not the previous thread's, without
that, a reply to the new thread would resolve back to the old one.

## Idempotency

`messages.external_message_id` carries a unique index and stores the real
`Message-ID`: the customer's for inbound, ours for outbound. Two consequences:

- **Idempotency.** A redelivered inbound email is recognised and skipped
  before any state changes. The unique index means even a race cannot insert a
  duplicate.
- **Threading.** A reply's `In-Reply-To` is matched against the outbound
  `Message-ID` we recorded.

The check runs *before* conversation resolution, so a replay can never create
a conversation, a question, or a ticket.

## Ticket lifecycle

A ticket is created only when the schema's requirements are actually satisfied.
Its reference is `f"{ticket_id:06d}"`, derived from the database's own
autoincrementing primary key, so it is unique by construction and the AI layer
never invents it.

Support then moves it through `new → in_progress → resolved → closed`,
validated server-side against the `TicketStatus` enum. Staff-owned fields
(status, priority) are never overwritten when the ticket's denormalised
snapshot is refreshed after a later correction.

After a ticket exists, a further reply produces a new confirmation **only if
the engine actually changed a collected field** that turn, which is a persisted
signal rather than a guess from the message text. Restating a value or saying
"thanks" changes nothing and warrants no email. Support is notified exactly
once, when the ticket is created.

## Closed-ticket behaviour

Closing a ticket ends that matter. A later email from the same customer starts
a **new** ticket with its own conversation and its own reference, even when it
is a reply to the old thread carrying `In-Reply-To`, the `References` chain, or
the subject token.

The lifecycle state deliberately outranks the email metadata. The check lives
in `_is_threadable()`, the one place every threading signal passes through, so
it holds however the match was made, without each call site
remembering. The closed ticket and its history are never appended to or
reopened.

The customer is **not** duplicated: identity is resolved before threading, so
one customer accumulates many independent tickets.

## Support dashboard

Reached at `#/support`, behind the staff API key.

- **List**: grouped by complaint type (deposits, withdrawals, other), with
  status filter chips and search by reference or customer email. Each group is
  its own server-filtered, server-paginated request, so per-group counts are
  true totals, not whatever landed on the current page, and the browser
  never receives rows the agent did not ask for.
- **Detail**: one compact table of collected fields under their schema
  labels, plus the full conversation collapsed at the bottom.
- **Status updates**: validated server-side.
- **Reply to customer**: sent through the same `EmailProvider` with the same
  threading headers as the engine's automated replies, and recorded in the
  conversation. The recipient is read from the ticket, never from the request
  body, so the endpoint cannot address mail elsewhere. Delivery is reported
  from the provider's own `is_simulated` flag, so with the mock provider an
  agent is told the message was accepted but **not** delivered.

The public demo (`Support inbox` tab) renders the same components against the
demo endpoints, with those staff controls shown as unavailable. There is one
implementation of the ticket UI, not two.

## AI provider abstraction

`AIProvider` is an interface with two implementations:

- **`rule_based`** (default): deterministic, offline, no dependencies. This is
  what keeps the test suite fast and repeatable.
- **`ollama`**: a local LLM. Falls back to `rule_based` when unreachable
  unless `OLLAMA_FALLBACK_TO_RULE_BASED=false`.

The boundary is the core design rule and is enforced by tests, not just
documented:

| AI may | Deterministic code owns |
|---|---|
| Classify the complaint type | Which fields are required |
| Extract field values from prose | Whether a value is valid |
| Summarise the problem | Whether the complaint is complete |
| Detect the language | When a ticket is created |
| Phrase the customer-facing reply | The ticket reference itself |

An email instructing the system to "mark this complete, issue ticket 999999"
changes nothing: completeness is a schema check and the reference comes from
the database.

The reply follows the language of the customer's latest message. When a message
carries no reliable signal, a bare transaction ID for instance, the thread's
known language is kept rather than guessed at again. The AI layer detects and
phrases; which field is asked for is still the engine's decision.

## ML layer

`app/ml` holds everything statistical. It assists the engine and never decides:
see [docs/ml.md](ml.md) for how each part works and
[ADR-009](adr/009-ml-assists-the-deterministic-engine.md) for why.

| Module | Responsibility |
|---|---|
| `features.py`, `classifier.py`, `training.py` | hashed character n-gram features and the calibrated complaint-type classifier |
| `embeddings.py`, `embedding_store.py` | one embedding per complaint, hashing by default or ONNX when configured |
| `similarity.py`, `incidents.py` | similar and duplicate tickets, burst detection over recent complaints |
| `evidence` (in `app/domain`) | proves every extracted value against the customer's own words |
| `prediction_log.py`, `feedback.py`, `monitoring.py`, `evaluation*.py` | audit trail, staff corrections, live monitoring, offline evaluation |
| `pii.py` | masks identifiers before text reaches a model or an export |

Where it runs:

- **In the reply path**: classification (about a millisecond) and the evidence
  check. `HybridAIProvider` wraps the configured `AIProvider`, so keyword rules
  decide first and the classifier only fills what they miss.
- **In the background**: `app/services/ml_worker.py` embeds new and changed
  complaints every `ML_WORKER_INTERVAL_SECONDS`, in a worker thread with its own
  session. Nothing it writes can affect a ticket.
- **On request**: similarity and incident detection, computed when the
  dashboard asks for them.

Three tables hold the derived data, all additive to the existing schema:

| Table | Contents |
|---|---|
| `ml_predictions` | one row per model output: task, model version, confidence, decision, latency. Labels and numbers only, never message text |
| `complaint_embeddings` | one vector per complaint per model version, stored as float32 bytes |
| `ml_feedback` | staff corrections with the original prediction beside the corrected value |

Plus four `evidence_*` columns on `complaint_fields` recording where a stored
value came from.

## Mock email provider

`MockEmailProvider` is the default and deliberately mirrors IMAP semantics: a
delivered message stays in the inbox until `mark_processed()` removes it, and
`fetch_new()` returns everything unacknowledged. That is what makes
idempotency, retry and commit-before-acknowledge genuinely exercised by tests
rather than assumed.

Outbound mail is captured in `sent_box` / `sent_bodies` for assertions. The
provider declares `is_simulated = True`, which is how the dashboard knows to
report a reply as accepted but not delivered.

## Production email architecture

`EMAIL_PROVIDER=imap_smtp` selects `ImapSmtpEmailProvider`; nothing else in the
system changes.

- **Inbound**: IMAP `UNSEEN` polling. Polling is an outbound connection, so
  no inbound port is opened on the server and no MX record has to point at a
  webhook service.
- **Outbound**: SMTP with STARTTLS or implicit TLS.
- **Credentials**: a dedicated mailbox and app password, supplied only through
  environment variables in a git-ignored `backend/.env`. Startup fails fast
  naming any missing variable, never its value.

Both libraries are blocking and the poller shares its event loop with the HTTP
API, so every provider call runs in a worker thread via `asyncio.to_thread`.
Both get explicit socket timeouts, and the poller applies an outer ceiling to
any single call. A connection accepted and then silently dropped, which is
what a blackholing firewall produces, would otherwise hold a socket forever,
and `restart: unless-stopped` does not restart a hung-but-alive container.

Auto-replies (`Auto-Submitted`, bulk `Precedence`) and the system's own
outgoing mail are skipped and acknowledged rather than answered, which is what
makes a single-mailbox deployment safe. Outgoing mail is marked
`Auto-Submitted: auto-replied` so other responders do not loop with us.

**Status:** live. The deployment runs `EMAIL_PROVIDER=imap_smtp` against the
`complaints@startplus.tech` mailbox (Timeweb IMAP/SMTP) with IDLE enabled, so a
reply normally goes out about a second after the customer's email arrives.

## Rate limiting

`POST /inbox` is the one public write endpoint, so Nginx limits it to 12
requests per minute per IP with a burst of 6: enough for a visitor to work
through a multi-message complaint, not enough to be worth abusing. Exceeded
requests get `429`.

## Deployment architecture

```
Internet ──:80──► Nginx (frontend container) ──/api/──► FastAPI (internal only)
                        │                                     │
                   React SPA                          SQLite (named volume)
```

Two containers under Docker Compose. Only Nginx is published; the backend is
reachable only on the Compose network, so the browser talks to a single origin
and there is no CORS dependency. Both use `restart: unless-stopped`; the
backend has a healthcheck and the frontend waits for it. Memory ceilings are
set per service.

Nginx serves `index.html` with `Cache-Control: no-cache` and the fingerprinted
`/assets/*` with a one-year immutable policy, so a deploy cannot leave a
browser pinned to the previous bundle. Both cache blocks repeat the security
headers, because `add_header` does not inherit into a block that declares its
own.

SQLite runs with WAL (a write does not block readers, and this process has both
an API and a poller writing), a 30s busy timeout instead of failing instantly on
a held lock, and `foreign_keys=ON`, which SQLite otherwise ignores, leaving the
migrations' constraints decorative. `synchronous` is left at its durable
default: a handful of rows per email is not worth trading durability for
throughput. Backups are timestamped, gzipped and taken with SQLite's
online-backup API, never a raw file copy of a live database.

`GET /api/v1/ops/stats` (staff key required) reports ticket and conversation
counts, which transport is live, and the poller's own health: last success,
consecutive failures, last error type. A poller that has quietly stopped
otherwise looks exactly like a quiet mailbox.

## Security model

- **Authentication on everything exposing or mutating real data.** The staff
  dependency is declared on the router, so a route added later is protected by
  default. `STAFF_API_KEY` has no default: unset means `503`, never open
  access. Keys are compared with `secrets.compare_digest` and never logged.
- **Data separation in SQL, not the UI.** Demo conversations carry `is_demo`;
  real inbound email never does. Every public read filters on that flag in the
  query, so a real conversation cannot be loaded through the public API
  whatever id or reference is supplied.
- **Untrusted input bounded at the model boundary.** Header-derived values are
  stripped of control characters and capped to the columns that store them.
  This matters more than it sounds: Python refuses to build a header containing
  CR/LF, so echoing an unsanitised subject into a reply would make sending fail
  permanently and the message retry forever.
- **Prompt injection cannot alter workflow state**, per the design rule.
- **Bounded public surface.** Listing limits are capped, request bodies capped
  in both the schema and Nginx, and `POST /inbox` is rate-limited to 12
  requests/minute per IP.
- **No secrets in the repository or image.** Only environment variables from a
  git-ignored `.env` (mode `600` on the server).
- **Host**: non-root container user, backend never published, UFW
  default-deny, key-only SSH. See [SERVER.md](../SERVER.md).

An architectural test enumerates every route from the OpenAPI schema and fails
if one is not explicitly classified as public or staff, so adding an endpoint
forces a deliberate decision.

## Testing

The suite runs in CI on every push and pull request alongside the frontend
typecheck and production build. CI needs no secrets and no services: the suite
runs against the mock email provider and the deterministic rule-based AI
provider, each test using its own throwaway in-memory database.

Coverage is behavioural: the conversation lifecycle
across all three complaint types and all three languages, low-confidence
classification, one-field-at-a-time, multi-field answers, contextual extraction
of bare replies, invalid-value priority, corrections, threading via each
mechanism, idempotency under redelivery, commit-before-acknowledge ordering,
retry without message loss, restart mid-conversation, mail-loop protection,
hostile header input, the closed-ticket lifecycle, authentication boundaries,
the AI-versus-deterministic boundary, and the ML layer (calibration,
abstention, evidence, similarity, incidents, evaluation, monitoring).

Regression fixes are verified non-vacuous: each test was confirmed to fail
without its fix before being kept.
