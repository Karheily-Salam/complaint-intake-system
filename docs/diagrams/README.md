# Architecture diagrams

Nine diagrams of the system as it is built, drawn from the code rather than
from intention. Every diagram is shown in full on this page; each one also has
its own file with the sources it was drawn from and the notes that go with it.

| # | Diagram | Read it when |
|---|---|---|
| 01 | [System context and containers](#1-system-context-and-containers) | You are new to the project, or need to know what is deployed |
| 02 | [Backend and service architecture](#2-backend-and-service-architecture) | Finding where a responsibility lives, or adding a route or service |
| 03 | [Email intake sequence](#3-email-intake-sequence) | Changing the poller or `IntakeService`, or reasoning about retries and crash safety |
| 04 | [Thread resolution and idempotency](#4-thread-resolution-and-idempotency) | Touching threading, the demo boundary, or closed-ticket behaviour |
| 05 | [Conversation engine turn](#5-conversation-engine-turn) | Changing business logic, validation, or what the customer is asked next |
| 06 | [ML architecture and data flow](#6-ml-architecture-and-data-flow) | Changing anything under `app/ml`, or checking that ML cannot mutate a ticket |
| 07 | [Data model](#7-data-model) | Writing a query or a migration |
| 08 | [Conversation, complaint and ticket lifecycle](#8-conversation-complaint-and-ticket-lifecycle) | Changing status handling |
| 09 | [Production deployment](#9-production-deployment) | Before a deploy, or tracing how a commit becomes a container |

Start with 1 for the shape of the system, then 3 to see a real email move
through it. 2 tells you where the code is. After that read whichever of 4 to 9
matches what you are about to change.

## 1. System context and containers

Who uses the system, what runs where, and which external services it depends on.

```mermaid
flowchart LR
    CUST["Customer<br/>any mail client"]
    AGENT["Support agent<br/>browser"]
    VISITOR["Demo visitor<br/>browser"]

    MAILBOX["Mailbox provider<br/>IMAP and SMTP"]
    OLLAMA["Ollama local LLM<br/>optional, AI_PROVIDER=ollama"]

    subgraph host["VPS, Docker Compose"]
        subgraph fe["frontend container, nginx 1.27-alpine"]
            PROXY["Nginx<br/>host port 80<br/>rate limit on POST /api/v1/inbox"]
            SPA["Built React SPA"]
        end

        subgraph be["backend container, uvicorn on 8000, expose only"]
            API["FastAPI app<br/>public and staff routers"]
            POLLER["EmailPoller task<br/>runs when EMAIL_PROVIDER is not mock"]
            MLW["ML worker task<br/>runs when ML_WORKER_ENABLED"]
        end

        DB[("SQLite on backend_data volume<br/>/app/data/complaint_intake.db")]
    end

    CUST -->|"sends and receives email"| MAILBOX
    MAILBOX -->|"IDLE notification, then UNSEEN fetch"| POLLER
    POLLER -->|"SMTP reply and ticket notification"| MAILBOX

    VISITOR -->|"no credential: POST /inbox, /demo/*, /schemas, /health"| PROXY
    AGENT -->|"X-API-Key: /tickets, /conversations, /ops, /ml"| PROXY

    PROXY --> SPA
    PROXY -->|"proxies /api/ to backend:8000"| API

    API --> DB
    POLLER --> DB
    MLW --> DB

    API -.->|"only when configured"| OLLAMA
```

Detail and sources: [01-system-context.md](01-system-context.md).

## 2. Backend and service architecture

The modules inside `backend/app`, and which way their dependencies point.

```mermaid
flowchart TD
    subgraph public["Public routers, no credential"]
        R_HEALTH["health"]
        R_SCHEMAS["schemas"]
        R_INBOX["inbox"]
        R_DEMO["demo"]
    end

    subgraph staff["Staff routers, StaffAuth on the router"]
        R_TICKETS["tickets"]
        R_CONV["conversations"]
        R_OPS["ops"]
        R_ML["ml"]
    end

    AUTH["api/security.py<br/>require_staff_api_key<br/>constant time, fails closed with 503"]

    subgraph services["Services"]
        INTAKE["IntakeService<br/>orchestration and transaction boundary"]
        POLLER["EmailPoller<br/>background loop"]
        TICKETS["TicketService"]
        REPLY["TicketReplyService"]
        CORRECT["CorrectionService"]
        CONVSVC["ConversationService"]
        MLWORKER["ml_worker<br/>background loop"]
    end

    ENGINE["ConversationEngine<br/>deterministic, no I/O"]

    subgraph interfaces["Provider interfaces"]
        AIBASE["AIProvider"]
        EMAILBASE["EmailProvider"]
    end

    subgraph aiimpl["AI implementations"]
        HYBRID["HybridAIProvider<br/>wraps the base provider"]
        RULES["RuleBasedAIProvider"]
        OLLAMA["OllamaAIProvider"]
    end

    subgraph emailimpl["Email implementations"]
        MOCK["MockEmailProvider<br/>is_simulated"]
        IMAP["ImapSmtpEmailProvider<br/>plus idle.py"]
    end

    subgraph domain["Domain"]
        REGISTRY["complaint_schemas<br/>registry and spec, YAML definitions"]
        VALID["validation.py"]
        EVID["evidence.py"]
    end

    ML["app/ml<br/>classifier, embeddings, similarity,<br/>incidents, monitoring, feedback"]

    subgraph data["Persistence"]
        REPOS["Repositories<br/>Customer, Conversation, Ticket"]
        MODELS["SQLAlchemy models"]
        DB[("SQLite")]
        ALEMBIC["Alembic migrations<br/>the only schema authoring path"]
    end

    R_TICKETS --> AUTH
    R_CONV --> AUTH
    R_OPS --> AUTH
    R_ML --> AUTH

    R_INBOX --> INTAKE
    R_DEMO --> CONVSVC
    R_DEMO --> TICKETS
    R_SCHEMAS --> REGISTRY
    R_TICKETS --> TICKETS
    R_TICKETS --> REPLY
    R_TICKETS --> CORRECT
    R_TICKETS --> ML
    R_CONV --> CONVSVC
    R_ML --> ML
    R_OPS -->|"counts and timestamps only"| MODELS
    R_OPS -->|"poller_health snapshot"| POLLER

    POLLER --> INTAKE
    INTAKE --> ENGINE
    INTAKE --> TICKETS
    INTAKE --> REPOS
    INTAKE --> EMAILBASE
    INTAKE --> ML
    REPLY --> EMAILBASE
    CORRECT --> TICKETS
    CORRECT --> ML
    MLWORKER --> ML

    ENGINE --> AIBASE
    ENGINE --> REGISTRY
    ENGINE --> VALID
    ENGINE --> EVID

    AIBASE --- HYBRID
    HYBRID --> RULES
    HYBRID --> OLLAMA
    HYBRID --> ML
    EMAILBASE --- MOCK
    EMAILBASE --- IMAP

    REPOS --> MODELS
    ML --> MODELS
    MODELS --> DB
    ALEMBIC --> DB
```

Detail and sources: [02-backend-architecture.md](02-backend-architecture.md).

## 3. Email intake sequence

One inbound email, from the IMAP notification to the acknowledgement on the server.

```mermaid
sequenceDiagram
    autonumber
    participant M as Mailbox server
    participant EP as ImapSmtpEmailProvider
    participant P as EmailPoller
    participant IS as IntakeService
    participant CE as ConversationEngine
    participant AI as AIProvider (hybrid)
    participant TS as TicketService
    participant DB as SQLite session

    M-->>EP: IDLE notification
    P->>EP: wait_for_activity(interval)
    EP-->>P: notified
    P->>EP: fetch_new()
    EP->>M: UID SEARCH UNSEEN, then FETCH
    EP-->>P: list of InboundEmail

    loop each email, independently
        P->>P: _loop_risk(inbound)
        alt self addressed or auto submitted
            P->>EP: mark_processed(message_id)
            Note over P,EP: skipped, not processed
        else genuine customer mail
            P->>IS: handle_inbound_email(inbound)
            IS->>DB: find_by_external_message_id(message_id)
            alt already processed
                IS-->>P: None
                P->>EP: mark_processed(message_id)
            else new message
                IS->>DB: get_or_create customer, resolve conversation
                IS->>DB: add inbound Message and EmailLog
                IS->>CE: advance(state)
                CE->>AI: detect_language, classify, extract, summarize
                AI-->>CE: results
                CE->>AI: compose_reply, unless complete
                CE-->>IS: EngineOutcome
                IS->>DB: persist ComplaintFields and ml_predictions
                opt outcome.is_complete
                    IS->>TS: create_for_complaint or refresh_snapshot
                    TS-->>IS: ticket with reference
                    IS->>CE: compose_ticket_confirmation(reference)
                    CE-->>IS: confirmation body
                end
                IS->>EP: send(reply)
                EP->>M: SMTP
                IS->>DB: add outbound Message and EmailLog
                opt new ticket
                    IS->>EP: send(ticket notification to support inbox)
                end
                IS->>DB: commit
                IS-->>P: IntakeResult
                P->>EP: mark_processed(message_id)
            end
        end
    end
```

Detail and sources: [03-email-intake-sequence.md](03-email-intake-sequence.md).

## 4. Thread resolution and idempotency

How a reply is matched to a conversation, and what disqualifies a match.

```mermaid
flowchart TD
    START["Inbound email"]
    IDEM{"Message-ID already in<br/>messages.external_message_id?"}
    SKIP["Return None<br/>caller still acknowledges it"]
    CUST["get_or_create customer<br/>identity is email plus is_demo"]

    IRT{"In-Reply-To matches a<br/>Message-ID we sent?"}
    REFS{"Any References entry,<br/>newest first, matches?"}
    TOKEN{"Subject carries a<br/>[Ref:token] we issued?"}

    CHECK1["_is_threadable"]
    CHECK2["_is_threadable"]
    CHECK3["_is_threadable"]

    subgraph gate["_is_threadable, one gate for every signal"]
        G1{"Conversation exists and<br/>belongs to this customer?"}
        G2{"Conversation is_demo?"}
        G3{"Its ticket status is closed?"}
        OK["Threadable"]
        NO["Not threadable"]
    end

    CONTINUE["Continue that conversation"]
    NEWCONV["Create a new conversation<br/>subject stripped of any inherited token<br/>same customer, new thread_token"]

    START --> IDEM
    IDEM -- yes --> SKIP
    IDEM -- no --> CUST --> IRT
    IRT -- match --> CHECK1
    IRT -- no match --> REFS
    REFS -- match --> CHECK2
    REFS -- no match --> TOKEN
    TOKEN -- match --> CHECK3
    TOKEN -- no match --> NEWCONV

    CHECK1 --> G1
    CHECK2 --> G1
    CHECK3 --> G1

    G1 -- no --> NO
    G1 -- yes --> G2
    G2 -- yes --> NO
    G2 -- no --> G3
    G3 -- yes --> NO
    G3 -- no --> OK

    OK --> CONTINUE
    NO --> NEWCONV
```

Detail and sources: [04-thread-resolution.md](04-thread-resolution.md).

## 5. Conversation engine turn

Inside `ConversationEngine.advance()` for one message. No persistence, no email, no network.

```mermaid
flowchart TD
    IN["ConversationState<br/>latest message with quoted history stripped,<br/>collected fields, pending_field, language"]
    LANG["detect_language<br/>below MIN_LANGUAGE_CONFIDENCE keeps<br/>the thread's known language"]
    KNOWN{"Complaint type<br/>already known?"}
    CLASSIFY["classify against the schema registry options"]
    CONF{"Confident enough?<br/>MIN_CLASSIFICATION_CONFIDENCE"}
    CLARIFYTYPE["Reply asks what the problem is about<br/>status open, no pending field"]

    SCHEMA["Load schema, read fields_for()"]
    EXTRACT["extract against this schema's fields<br/>pending_field passed for bare answers"]

    subgraph merge["Merge, per extracted value"]
        VALIDATE["validate_field"]
        EVIDENCE{"locate_evidence finds a span<br/>in the customer's own message?"}
        REJECT["Rejected, recorded in the audit<br/>cannot overwrite anything"]
        MERGE["Merge rules:<br/>never clear on empty extraction,<br/>same value restated is not a change,<br/>a valid value may correct an earlier one,<br/>an invalid value never replaces a valid one"]
    end

    SUMMARY["summarize the inbound transcript"]
    OPEN{"Open schema, as in 'other'?"}
    ENOUGH{"Summary reaches<br/>min_description_words?"}
    CLARIFYVAGUE["Reply asks for a fuller description<br/>pending field problem_description"]

    CLASSIFYFIELDS["Compute missing and invalid in schema order,<br/>pick the first unresolved field"]
    DONE{"Anything unresolved?"}
    COMPLETE["is_complete, status validating<br/>no reply composed here:<br/>the ticket reference does not exist yet"]
    ASK["compose_reply for exactly one field:<br/>a correction if it is invalid,<br/>otherwise a plain request"]

    IN --> LANG --> KNOWN
    KNOWN -- yes --> SCHEMA
    KNOWN -- no --> CLASSIFY --> CONF
    CONF -- no --> CLARIFYTYPE
    CONF -- yes --> SCHEMA
    SCHEMA --> EXTRACT --> VALIDATE --> EVIDENCE
    EVIDENCE -- no --> REJECT
    EVIDENCE -- yes --> MERGE
    REJECT --> SUMMARY
    MERGE --> SUMMARY
    SUMMARY --> OPEN
    OPEN -- yes --> ENOUGH
    ENOUGH -- no --> CLARIFYVAGUE
    ENOUGH -- yes --> CLASSIFYFIELDS
    OPEN -- no --> CLASSIFYFIELDS
    CLASSIFYFIELDS --> DONE
    DONE -- no --> COMPLETE
    DONE -- yes --> ASK
```

Detail and sources: [05-conversation-engine.md](05-conversation-engine.md).

## 6. ML architecture and data flow

Every statistical component, split by when it runs, and what each one writes.

```mermaid
flowchart TD
    subgraph replypath["In the reply path, synchronous"]
        MSG["Customer message"]
        HYB["HybridAIProvider.classify"]
        RULES["keyword_classification<br/>deterministic, always wins"]
        CLF["LinearTextClassifier.predict<br/>hashed char n-grams, temperature scaled"]
        ART["artifacts/complaint_classifier.npz and .json"]
        SIGNAL["MLSignal<br/>label, confidence, abstained"]
        EXT["Engine extraction plus locate_evidence"]
        LOG["record_classification<br/>record_extraction"]
    end

    subgraph background["Background, ml_worker every ML_WORKER_INTERVAL_SECONDS"]
        WORKER["ml_worker.run_once"]
        EMBPEND["embed_pending<br/>new or stale complaints only"]
        EMB["Embedder<br/>HashingEmbedder default,<br/>OnnxE5Embedder when configured"]
        PII["pii.mask_identifiers<br/>before any text is embedded"]
    end

    subgraph onrequest["On request, staff routes"]
        SIM["find_similar<br/>cosine plus customer identity<br/>plus shared strong fields"]
        INC["detect_incidents<br/>average linkage clusters,<br/>Poisson burst test, c-TF-IDF labels,<br/>per type volume spikes"]
        MON["monitoring_snapshot<br/>decided_by mix, abstention,<br/>shadow agreement, latency, PSI drift"]
        REG["model_registry"]
        FB["feedback_summary and recent_feedback"]
    end

    subgraph correction["Staff correction loop"]
        CORR["CorrectionService<br/>validated against the schema,<br/>source becomes employee"]
    end

    subgraph offline["Offline, run by hand"]
        TRAIN["scripts/train_classifier.py<br/>datasets/complaints/train.jsonl"]
        EVAL["scripts/evaluate_ml.py<br/>test.jsonl and extraction_test.jsonl"]
        EXPORT["scripts/export_training_feedback.py<br/>masked export"]
        DL["scripts/download_embedding_model.py"]
    end

    T_PRED[("ml_predictions")]
    T_EMB[("complaint_embeddings")]
    T_FB[("ml_feedback")]
    T_FIELDS[("complaint_fields<br/>evidence_* columns")]
    T_TICKET[("tickets and complaints")]
    REPORT["docs/ml/evaluation.md and .json"]

    MSG --> HYB
    HYB --> RULES
    HYB --> CLF
    CLF --> ART
    RULES --> SIGNAL
    CLF --> SIGNAL
    SIGNAL --> LOG
    MSG --> EXT
    EXT --> LOG
    EXT --> T_FIELDS
    LOG --> T_PRED

    WORKER --> EMBPEND --> PII --> EMB --> T_EMB

    T_EMB --> SIM
    T_EMB --> INC
    T_PRED --> MON
    T_FB --> FB
    ART --> REG

    CORR --> T_FB
    CORR --> T_TICKET
    T_PRED -.->|"the prediction being corrected"| CORR

    TRAIN --> ART
    EVAL --> REPORT
    EXPORT --> T_FB
    DL -.->|"optional model files"| EMB
```

Detail and sources: [06-ml-architecture.md](06-ml-architecture.md).

## 7. Data model

Every table, its foreign keys, and the columns that carry a rule.

```mermaid
erDiagram
    customers ||--o{ conversations : "opens"
    customers ||--o{ tickets : "owns"
    conversations ||--o{ messages : "contains"
    conversations ||--o| complaints : "has at most one"
    conversations ||--o{ email_logs : "records"
    complaints ||--o{ complaint_fields : "collects"
    complaints ||--o| tickets : "becomes"
    complaints ||--o{ complaint_embeddings : "is vectorised as"
    messages ||--o{ complaint_fields : "is the source of"
    conversations ||--o{ ml_predictions : "produces"
    messages ||--o{ ml_predictions : "produces"
    tickets ||--o{ ml_feedback : "is corrected by"
    ml_predictions ||--o{ ml_feedback : "is judged by"

    customers {
        int id PK
        string email "unique together with is_demo"
        bool is_demo "separates demo from real identity"
        string name
    }

    conversations {
        int id PK
        int customer_id FK
        bool is_demo "every public read filters on this"
        string subject
        string thread_token "unique, the [Ref:token] fallback"
        string status "ConversationStatus"
        string language_code "reply language, kept when a message is ambiguous"
        string pending_field "the field a bare answer is read against"
    }

    messages {
        int id PK
        int conversation_id FK
        string direction "inbound or outbound"
        string external_message_id "unique index, carries idempotency and threading"
        string body "stored raw, quoted history is stripped downstream"
        json raw_meta "in_reply_to, references, received_at"
    }

    complaints {
        int id PK
        int conversation_id FK "unique"
        string type "withdrawal, deposit, other"
        string method_key "denormalised deposit method, never branched on"
        string concise_description
        string status "ComplaintStatus"
    }

    complaint_fields {
        int id PK
        int complaint_id FK "unique together with key"
        string key
        string value
        string status "FieldStatus"
        string source "customer_message, ai_inference, employee"
        int source_message_id FK
        float confidence
        string evidence_text "the customer words supporting the value"
        int evidence_start
        int evidence_end
        string evidence_method "exact, date, normalized, overlap"
    }

    tickets {
        int id PK
        string reference "unique, formatted from the ticket id"
        int complaint_id FK "unique"
        int conversation_id FK
        int customer_id FK
        string type
        string status "TicketStatus, closed is terminal for threading"
        string priority
        json structured_data "denormalised snapshot of the fields"
    }

    email_logs {
        int id PK
        int conversation_id FK
        string direction
        string provider "the transport that actually handled it"
        string subject
        string body
    }

    ml_predictions {
        int id PK
        string task "classification or extraction"
        string model_name
        string model_version
        int conversation_id FK
        int message_id FK
        bool is_demo
        string predicted_label
        float confidence
        bool abstained
        string final_label "what the system acted on"
        string decided_by "rules, ml or provider"
        float latency_ms
        json details "no message text, addresses or values"
    }

    complaint_embeddings {
        int id PK
        int complaint_id FK "unique together with model_version"
        string model_name
        string model_version "vectors from different models never mix"
        int dim
        bytes vector "float32 bytes"
        string text_sha256 "detects whether the text really changed"
    }

    ml_feedback {
        int id PK
        string kind "classification or field"
        int ticket_id FK
        int complaint_id FK
        int conversation_id FK
        int prediction_id FK "the prediction this corrects, when known"
        string field_key
        string original_value
        string corrected_value
        string original_source
        float original_confidence
        string model_version
        bool exported
    }
```

Detail and sources: [07-data-model.md](07-data-model.md).

## 8. Conversation, complaint and ticket lifecycle

The three status enums, what moves each one, and how they interact.

**Conversation**

```mermaid
stateDiagram-v2
    [*] --> open: first email, type not yet known
    open --> open: still too vague to classify
    open --> collecting_info: type decided, fields outstanding
    collecting_info --> collecting_info: one field asked per message
    collecting_info --> validating: every required field valid
    validating --> completed: ticket created
    completed --> completed: later reply, no collected field changed
    completed --> [*]

    note right of validating
        No reply is composed here.
        IntakeService creates the ticket first,
        so the confirmation carries a real reference.
    end note
```

**Complaint**

```mermaid
stateDiagram-v2
    [*] --> draft: model default, created with the conversation
    draft --> collecting: type known, fields outstanding
    collecting --> ready: schema requirements satisfied
    ready --> ticketed: ticket created
    ticketed --> ready: a later turn is complete again
    ticketed --> [*]

    note right of ticketed
        Set only when the ticket row is created.
        refresh_snapshot does not set it again,
        so a later complete turn leaves the
        complaint at ready while its ticket stands.
    end note
```

**Ticket**

```mermaid
stateDiagram-v2
    [*] --> new: created when the complaint is complete

    state "any status, set directly by staff" as pool {
        new
        in_progress
        resolved
        closed
    }

    closed --> [*]

    note right of pool
        The service checks membership of the enum,
        not the path taken, so these four form a
        fully connected set rather than a pipeline.
        new is the only one the system itself sets.
    end note
```

Detail and sources: [08-lifecycle.md](08-lifecycle.md).

## 9. Production deployment

What is built, what runs on the host, and what surrounds it.

```mermaid
flowchart TD
    subgraph repo["Repository"]
        SRC["backend/ and frontend/ sources"]
        CI["GitHub Actions CI<br/>ruff, pytest, typecheck, production build<br/>no secrets, no services"]
    end

    subgraph build["docker compose build"]
        BEIMG["complaint-intake-backend<br/>python:3.12-slim, editable install,<br/>non-root appuser, uvicorn on 8000"]
        FEIMG["complaint-intake-frontend<br/>node build stage, then nginx:1.27-alpine"]
    end

    subgraph vps["VPS, Ubuntu, UFW default deny"]
        subgraph compose["Docker Compose project"]
            FE["frontend service<br/>published on host port 80<br/>memory limit 128M"]
            BE["backend service<br/>expose 8000 only, healthcheck on /api/v1/health<br/>memory limit 384M"]
            VOL[("backend_data volume<br/>/app/data/complaint_intake.db")]
        end
        ENVFILE["backend/.env<br/>git ignored, mode 600, optional at build"]
        BACKUP["ops/scripts/backup-sqlite.sh<br/>SQLite online backup API, gzipped,<br/>timestamped, 14 day retention"]
        HEALTH["ops/scripts/health-check.sh<br/>disk, memory, load, container status"]
        BACKUPDIR[("/opt/backups")]
        LOGFILE["/var/log/server-health.log"]
    end

    INTERNET["Internet"]
    MAILBOX["Mailbox provider<br/>IMAP and SMTP"]

    SRC --> CI
    SRC --> BEIMG
    SRC --> FEIMG
    BEIMG --> BE
    FEIMG --> FE

    INTERNET -->|"port 80 only"| FE
    FE -->|"proxies /api/ on the compose network"| BE
    BE --- VOL
    ENVFILE -.->|"env_file, required false"| BE
    BE <-->|"outbound only, no inbound port"| MAILBOX

    BE -->|"RUN_MIGRATIONS_ON_STARTUP<br/>Alembic upgrade head in lifespan"| VOL
    BACKUP --> VOL
    BACKUP --> BACKUPDIR
    HEALTH --> LOGFILE
```

Detail and sources: [09-deployment.md](09-deployment.md).

## Where the prose lives

These diagrams are a companion to the written documentation, not a replacement:

- [docs/architecture.md](../architecture.md) is the component-level reference.
- [docs/ml.md](../ml.md) explains the ML layer in narrative form.
- [docs/adr/](../adr/) records why each significant decision was taken.

## Keeping them honest

A diagram that drifts is worse than none. When you change a component name, a
route, a table, or a status, update the diagram in the same commit. Each
diagram file lists the sources it was drawn from, so checking one is a matter
of rereading those files.

This page repeats the Mermaid source held in those files so that everything is
visible in one place. Edit the diagram file first, then copy the block here, and
the two stay identical.
