# System context and containers

**Scope.** Who uses the system, what runs where, and which external services it
depends on. One level above the code: no classes, no functions.

**Read it** first, or when you need to know what is deployed and what talks to
what.

Sources: `compose.yml`, `frontend/nginx.conf`, `backend/Dockerfile`,
`frontend/Dockerfile`, `backend/app/main.py`, `backend/app/core/config.py`.

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

Notes taken from the configuration, not assumed:

- The backend port is never published to the host. Only Nginx is, on port 80.
- Both background tasks are started in `lifespan()` in the same process as the
  API, not as separate services.
- With the default `EMAIL_PROVIDER=mock` no mailbox is involved and the poller
  is not started at all.
