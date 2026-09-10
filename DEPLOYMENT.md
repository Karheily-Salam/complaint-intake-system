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
docker compose logs -f backend      # expect "Email poller started (every 60s, ...)"
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

### Current status: staged for the Timeweb mailbox

`backend/.env` on the VPS is already filled in for `complaints@gateplus.ru`
except for the two passwords, and `EMAIL_PROVIDER` is still `mock`. Confirm
with:

```bash
docker compose exec backend python -m scripts.check_email
# -> FAIL  missing variables: IMAP_PASSWORD, SMTP_PASSWORD
```

Staged values: `IMAP_HOST=imap.timeweb.ru:993` (SSL),
`SMTP_HOST=smtp.timeweb.ru:465` (implicit SSL, STARTTLS off),
`IMAP_USERNAME=SMTP_USERNAME=SUPPORT_INBOX_ADDRESS=complaints@gateplus.ru`,
`MAIL_DOMAIN=gateplus.ru`, `SMTP_FROM_ADDR` unset.

This is a **single-mailbox** setup: the mailbox that receives complaints also
receives the completed tickets. Loop protection means the system never reads
its own notifications as complaints; they arrive in that mailbox already marked
read, under a `[Ticket NNNNNN]` subject.

**Two blockers remain, both on Timeweb's side** (verified 2026-09-10):

1. **`gateplus.ru` does not resolve.** The `.ru` registry itself
   (`a.dns.ripn.net`) returns NXDOMAIN — no NS, no MX, no SPF. Until the domain
   is registered and delegated, no one can deliver mail to the mailbox and mail
   sent from it will be rejected by most receivers.
2. **SMTP submission to Timeweb is firewalled from this VPS.** All three
   `smtp.timeweb.ru` IPs silently drop 25/465/587, while `imap.timeweb.ru:993`
   connects fine and SMTP to unrelated providers works from the same host — so
   this is not a VPS egress block. Needs a Timeweb support ticket.

Verify both are fixed before switching:

```bash
host -t MX gateplus.ru                              # must return Timeweb MX records
python3 -c "import socket;socket.create_connection(('smtp.timeweb.ru',465),8)"   # must not hang
```

Then enter the password and go live:

```bash
nano backend/.env      # fill IMAP_PASSWORD and SMTP_PASSWORD (never via a shell
                       # command - it would land in shell history)
docker compose up -d
docker compose exec backend python -m scripts.check_email
docker compose exec backend python -m scripts.check_email --send-test-to you@example.com
sed -i 's/^EMAIL_PROVIDER=mock/EMAIL_PROVIDER=imap_smtp/' backend/.env
docker compose up -d
docker compose logs -f backend    # expect "Email poller started (every 60s, ...)"
```

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
