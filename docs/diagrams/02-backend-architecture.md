# Backend and service architecture

**Scope.** The modules inside `backend/app`, and which way their dependencies
point. Routes at the top, storage at the bottom, interfaces in between.

**Read it** when you need to find where a responsibility lives, or before
adding a route or a service.

Sources: `app/api/routes/*`, `app/api/security.py`, `app/services/*`,
`app/conversation/engine.py`, `app/ai/*`, `app/email/*`, `app/repositories/*`,
`app/domain/*`.

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

Notes taken from the code:

- `ConversationEngine` imports `AIProvider` and the schema registry only. It
  performs no I/O and holds no session, which is why the same engine serves the
  demo endpoint and the mail poller.
- `HybridAIProvider` is built by `app/ai/factory.py` and wraps whichever base
  provider is configured. It overrides `classify` and delegates everything else.
- Staff authentication is declared on the router, so a new staff route inherits
  it by default. `tests/test_route_authorization_contract.py` fails if a route
  is missing from the policy.
