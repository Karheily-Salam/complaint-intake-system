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
