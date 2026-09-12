# Production deployment

**Scope.** What is built, what runs on the host, and what surrounds it:
migrations on startup, backups, health logging, and CI. Values specific to the
live deployment are deliberately absent.

**Read it** before a deploy, or when tracing how a commit becomes a running
container. Procedure: [DEPLOYMENT.md](../../DEPLOYMENT.md). Host notes:
[SERVER.md](../../SERVER.md).

Sources: `compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`,
`frontend/nginx.conf`, `ops/scripts/*`, `.github/workflows/ci.yml`,
`backend/app/core/migrations.py`.

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

Facts worth keeping straight:

- There is no `environment:` block in `compose.yml`, deliberately. Compose gives
  it higher precedence than `env_file`, which would let defaults written there
  override a real deployment's `backend/.env`.
- `backend/.env` is declared `required: false`, so a fresh clone starts with the
  application's own demo-safe defaults: mock email, rule-based AI, no mailbox.
- Migrations run inside the container at startup, so a deploy is build, up, and
  the schema follows the code.
- The two ops scripts are cron-driven on the host. The schedule and crontab live
  on the server, not in this repository, so they are shown here as scripts
  rather than as a scheduler.
- Nothing in this repository configures TLS, DKIM, or DMARC.
