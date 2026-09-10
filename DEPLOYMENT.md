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

**What you need first (the one manual step):** a dedicated mailbox for
complaints (e.g. `complaints@yourdomain.com`, or a free mailbox to start with)
and an **app password** for it - not the account's own password, and not an
OAuth client secret. For Gmail this means enabling 2FA and creating an app
password; most other providers have the same feature under "app passwords" or
"mail client access".

Then, **on the server only** (`backend/.env` is git-ignored and never
committed):

```bash
cd /opt/projects/complaint-intake-system
nano backend/.env          # see the IMAP_*/SMTP_* block in backend/.env.example
docker compose up -d       # picks up the new env, no rebuild needed
docker compose logs -f backend | head -30
```

Set at minimum:

```
EMAIL_PROVIDER=imap_smtp
SUPPORT_INBOX_ADDRESS=support@yourdomain.com   # where finished tickets land
MAIL_DOMAIN=yourdomain.com
IMAP_HOST=… IMAP_USERNAME=… IMAP_PASSWORD=…
SMTP_HOST=… SMTP_USERNAME=… SMTP_PASSWORD=…
```

The backend validates this at startup and refuses to start if anything is
missing, naming the missing variables (never their values). On success the log
shows `Email poller started (every 60s, provider=imap_smtp)`.

Notes:

- Outbound only - IMAP/SMTP are connections *from* the VPS, so no firewall
  change and no inbound port are needed.
- Use a mailbox dedicated to this system: the poller marks messages `\Seen`
  as it processes them.
- To verify safely, email the complaints mailbox from your own address; the
  ticket notification goes to `SUPPORT_INBOX_ADDRESS`, so point that at
  yourself for the first run.
- To go back to a dry system at any time, set `EMAIL_PROVIDER=mock` and
  `docker compose up -d`.

## Updating

```bash
cd /opt/projects/complaint-intake-system
git pull
docker compose up -d --build
```

## Logs / troubleshooting

```bash
docker compose logs -f backend
docker compose logs -f frontend
docker compose exec backend ls -l /app/data     # DB file + WAL
```
