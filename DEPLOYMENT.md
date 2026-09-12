# Deployment

This covers building/running *this* project. For the shared VPS itself
(SSH/firewall model, the `deploy` user, backups, multi-project layout,
monitoring, HTTPS readiness) see [`SERVER.md`](SERVER.md).

The stack is two containers, orchestrated by `compose.yml`:

| Service    | Image base            | Published        | Role |
|------------|-----------------------|------------------|------|
| `backend`  | `python:3.12-slim`    | internal only    | FastAPI + Alembic under uvicorn on `:8000` |
| `frontend` | `nginx:1.27-alpine`   | host `:80`       | serves the built React SPA, reverse-proxies `/api/` → `backend:8000` |

- The browser only ever talks to `http://<host>/` — Nginx proxies the API, so
  there is no CORS dependency and no hard-coded backend URL in the frontend
  (`VITE_API_BASE_URL` defaults to the relative `/api/v1`).
- SQLite lives at `/app/data/complaint_intake.db` inside the backend container,
  persisted in the named volume `backend_data`.
- `AI_PROVIDER=rule_based`, `EMAIL_PROVIDER=mock`, `DEBUG=false` in production.
  `EMAIL_PROVIDER=mock` means no real mailbox is attached yet - see "Going live
  with real email" below; everything for `imap_smtp` is implemented and tested,
  it only needs mailbox credentials.
- Migrations run automatically on backend startup
  (`RUN_MIGRATIONS_ON_STARTUP=true`).

## First deployment on the VPS

```bash
cd /opt/projects/complaint-intake-system
git pull

# One-time: create the production env file (not in git).
cp backend/.env.example backend/.env
#   then edit backend/.env to the "Production (Docker)" values documented
#   at the bottom of backend/.env.example:
#     ENVIRONMENT=production
#     DEBUG=false
#     DATABASE_URL=sqlite:////app/data/complaint_intake.db
#     DB_ECHO=false
#     RUN_MIGRATIONS_ON_STARTUP=true
#     BACKEND_CORS_ORIGINS=["http://72.56.114.70"]
#     AI_PROVIDER=rule_based
#     EMAIL_PROVIDER=mock
#     MIN_CLASSIFICATION_CONFIDENCE=0.45

docker compose up -d --build
```

## Health / smoke checks (from the server)

```bash
docker compose ps                       # both services "running", backend "healthy"
curl -fsS http://localhost/api/v1/health # proxied FastAPI health JSON
curl -fsS http://localhost/ | head       # SPA index.html

# End-to-end: create a ticket through a simulated inbound email
curl -fsS -X POST http://localhost/api/v1/inbox \
  -H 'Content-Type: application/json' \
  -d '{"from_addr":"c@example.com","subject":"Withdrawal stuck","body":"My withdrawal has not arrived."}'

curl -fsS http://localhost/api/v1/tickets
```

## Going live with real email

Production currently runs `EMAIL_PROVIDER=mock`: the application is complete,
but it is not attached to a mailbox, so no customer mail is received or sent.
Switching it on is configuration only - no code change, no redeploy of a
different image.

**What you need first (the one manual step):** *two* addresses.

1. A **dedicated mailbox** the system polls and sends as, e.g.
   `complaints@yourdomain.com` or a free mailbox to start with, plus an **app
   password** for it - not the account's own password, and not an OAuth client
   secret. For Gmail: enable 2-Step Verification, then create an app password.
2. An address for finished tickets (`SUPPORT_INBOX_ADDRESS`). A separate
   inbox is preferable - tickets arrive unread in a staff mailbox. Pointing
   it at mailbox 1 is also supported when only one mailbox exists: loop
   protection means the system never reads its own notifications as
   complaints, but they arrive in that mailbox already marked read, so look
   for the `[Ticket NNNNNN]` subject prefix.

Then, **on the server only** (`backend/.env` is git-ignored and never
committed):

```bash
cd /opt/projects/complaint-intake-system
git pull                   # loop prevention + the pre-flight checker below
nano backend/.env          # fill the IMAP_*/SMTP_* block, leave EMAIL_PROVIDER=mock
docker compose up -d --build

# Pre-flight: credentials are loaded but nothing is polling yet. This
# authenticates against IMAP and SMTP and sends nothing.
docker compose exec backend python -m scripts.check_email

# Optional: prove sending works, to your own address only.
docker compose exec backend python -m scripts.check_email --send-test-to you@example.com
```

Only once that passes, flip the provider:

```bash
sed -i 's/^EMAIL_PROVIDER=mock/EMAIL_PROVIDER=imap_smtp/' backend/.env
docker compose up -d
docker compose logs -f backend      # expect "Email poller started (provider=imap_smtp, idle=on, ...)"
```

Minimum settings (values are examples - see `backend/.env.example` for the
full annotated block):

```
EMAIL_PROVIDER=imap_smtp
SUPPORT_INBOX_ADDRESS=you@example.com          # NOT the polled mailbox
MAIL_DOMAIN=gmail.com
IMAP_HOST=imap.gmail.com   IMAP_USERNAME=complaints@example.com   IMAP_PASSWORD=…
SMTP_HOST=smtp.gmail.com   SMTP_USERNAME=complaints@example.com   SMTP_PASSWORD=…
```

`SMTP_FROM_ADDR` is best left unset: it then defaults to `IMAP_USERNAME`, so
customer replies come back to the mailbox that is actually polled.

The backend validates this at startup and refuses to start if anything is
missing, naming the missing variables (never their values).

Notes:

- Outbound only - IMAP/SMTP are connections *from* the VPS, so no firewall
  change and no inbound port are needed.
- Use a mailbox dedicated to this system: the poller marks messages `\Seen`
  as it processes them, and anything already unread in the mailbox when you
  switch over is treated as a new complaint (`check_email` reports the count
  first).
- Automatic mail (out-of-office replies, bounces, list traffic) and the
  system's own mail are ignored rather than answered, so it cannot get into a
  reply loop with itself or another responder.
- To go back to a dry system at any time, set `EMAIL_PROVIDER=mock` and
  `docker compose up -d`. Nothing else changes.

### Current status: live on the Timeweb mailbox

Production has been running real email since 2026-09-11:

| Setting | Value |
|---|---|
| `EMAIL_PROVIDER` | `imap_smtp` |
| Mailbox (polled and sending) | `complaints@startplus.tech` |
| IMAP | `imap.timeweb.ru:993`, SSL, IDLE enabled |
| SMTP | `smtp.timeweb.ru:465`, implicit SSL (STARTTLS off) |
| `SUPPORT_INBOX_ADDRESS` / `MAIL_DOMAIN` | `complaints@startplus.tech` / `startplus.tech` |
| `SMTP_FROM_ADDR` | unset, so it defaults to the polled mailbox |

Confirm from the server:

```bash
docker compose exec backend python -m scripts.check_email     # -> OK for IMAP and SMTP
curl -s localhost/api/v1/health | grep email_provider          # -> "imap_smtp"
docker compose logs backend | grep "Email poller started"
# -> Email poller started (provider=imap_smtp, idle=on, max wait 60s)
```

This is a **single-mailbox** setup: the mailbox that receives complaints also
receives the completed tickets. Loop protection means the system never reads
its own notifications as complaints; they arrive in that mailbox already marked
read, under a `[Ticket NNNNNN]` subject.

Two things that used to block this are resolved: `startplus.tech` replaced the
unregistered `gateplus.ru`, and Timeweb unblocked outbound SMTP from this VPS
after a support ticket. The mailbox password was entered directly on the server
(`/opt/ops/scripts/set-mailbox-password.py`, which reads it without echoing and
keeps it out of shell history) and exists only in `backend/.env`.

Still outstanding: DKIM and DMARC records for `startplus.tech`, and HTTPS.

## Updating

```bash
cd /opt/projects/complaint-intake-system
git pull
docker compose up -d --build
```

Pending Alembic migrations run at startup (`RUN_MIGRATIONS_ON_STARTUP=true`),
so a release that adds tables needs no separate step - but **take a backup
first** when one does:

```bash
/opt/ops/scripts/backup-sqlite.sh /opt/projects/complaint-intake-system backend /app/data/complaint_intake.db complaint-intake-system
```

### Release notes: the ML layer

The release that adds machine-learning assistance (classification, extraction
evidence, embeddings, duplicate and incident detection, staff-correction
feedback, monitoring) needs one env line added to `backend/.env` before
`docker compose up -d --build`:

```
# Test artefacts that must never enter an ML dataset or evaluation.
ML_DATASET_EXCLUDED_TICKETS=["000005"]
```

Everything else uses production-safe defaults (`ML_CLASSIFIER_MODE=assist`,
`EXTRACTION_REQUIRE_EVIDENCE=true`, `EMBEDDING_PROVIDER=hashing`,
`ML_WORKER_ENABLED=true`). Measured backend memory with those defaults is
~97 MB, inside the existing 384 MB container limit. Enabling the optional
multilingual ONNX embeddings later needs that limit raised to at least 768 MB -
see [docs/ml.md](docs/ml.md).

## Logs / troubleshooting

```bash
docker compose logs -f backend
docker compose logs -f frontend
docker compose exec backend ls -l /app/data     # DB file + WAL
```
