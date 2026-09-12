# Server

Shared infrastructure notes for the Timeweb Cloud VPS this project (and any
future ones) run on. `DEPLOYMENT.md` covers this one project's own
build/deploy steps; this file covers the host itself.

Ideally this file moves to its own small "infra" repo once a second project
exists, since it isn't really about `complaint-intake-system` - it lives
here for now because this is the only repo with server access set up.

> **Superseded for host-level topics (2026-09-12).** The VPS has been
> restructured for multi-project hosting. The authoritative document now lives
> on the server at **`/opt/infra/ARCHITECTURE.md`**.
>
> What changed since this file was written:
> - **TLS is live.** `startplus.tech` + `www` serve HTTPS via Let's Encrypt.
>   `gateplus.ru` + `www` redirect to it. Certificates renew automatically.
> - **A shared edge proxy (Caddy) at `/opt/infra/edge` is the only thing bound
>   to :80/:443.** This project no longer publishes a host port; it is reached
>   over the external `edge` network by the alias `complaint-intake-web`. The
>   "give the new project a different host port" advice below is obsolete -
>   the shared-proxy migration it anticipated has now happened.
> - **`/opt/ops` moved to `/opt/infra`** (scripts, logs, cron paths). The old
>   directory is archived under `/opt/backups/pre-migration-*/ops-legacy`.
> - Backups now run through `/opt/infra/scripts/backup-run.sh`, driven by
>   per-project definitions in `/opt/infra/backup.d/`.
>
> Sections below on SSH, the `deploy` user, UFW, fail2ban, Docker daemon
> settings and swap remain accurate.

## At a glance

| | |
|---|---|
| Host | Timeweb Cloud VPS, Ubuntu 24.04 LTS, 1 vCPU, 2 GB RAM, 30 GB NVMe |
| Access | SSH key only (no passwords), `deploy` user (sudo) or `root` |
| Firewall | UFW: 22, 80, 443 open; everything else denied by default |
| Projects | `/opt/projects/<name>/`, one per project, each its own `docker compose` stack |
| This project | `/opt/projects/complaint-intake-system` - see `DEPLOYMENT.md` |
| Public URL | https://startplus.tech/ (TLS live since 2026-09-12) |

## SSH / access model

- **`deploy`** - the day-to-day admin/deployment account. Created with the
  *same* authorized SSH key root already used (copied server-side, never
  transmitted or displayed), so whoever could already SSH in as root can log
  in as `deploy` immediately with no new key to distribute. Member of
  `sudo` (passwordless, via `/etc/sudoers.d/deploy` - the account has no
  password at all, so NOPASSWD is what makes `sudo` usable rather than
  locking it out) and `docker` (so routine `docker compose` commands don't
  need `sudo` - note this is effectively root-equivalent access, a known
  Docker caveat, acceptable here since this is a single-operator box and
  `sudo` already grants root anyway).
- **`root`** - kept reachable, but key-only; password authentication is
  disabled account-wide. A drop-in config under `/etc/ssh/sshd_config.d/` holds
  the hardening, and it has to sort before the distro's own file because sshd
  takes the first match across included files. Root-by-key plus the Timeweb web
  console (out-of-band, independent of sshd) are the two recovery paths if
  `deploy` is ever unusable.
- **fail2ban** is enabled for `sshd` and bans repeated failed logins. Check
  bans with `fail2ban-client status sshd`.
- No new keys were generated and none were ever printed to a terminal or
  committed anywhere - the only key material in play is whatever you already
  used to reach root.

## Firewall (UFW)

Default-deny incoming, default-allow outgoing:

| Port | Rule | Why |
|---|---|---|
| 22/tcp | open | SSH |
| 80/tcp | open | HTTP (Nginx, all projects eventually share this) |
| 443/tcp | open | HTTPS (not yet used - no TLS cert exists yet, see below) |
| 10050/tcp | open, only from Timeweb's 3 monitoring IPs | provider's pre-installed `zabbix-agent-timeweb`, feeds the Timeweb dashboard |
| everything else | denied | including 8000 (backend) - never published to the host in the first place |

**Important Docker + UFW interaction**: Docker manipulates `iptables`
directly for any port a container *publishes* (`ports:` in compose), and
that traffic bypasses UFW's normal INPUT-chain filtering. UFW's rules above
correctly gate host-level services (SSH), but the real control for
container exposure is **never `ports:` a container port you don't want
public - use `expose:` for internal-only** (exactly what `backend` already
does). This matters for every future project, not just this one.

Check status: `ufw status verbose`.

## Docker

- `systemctl is-enabled docker` → `enabled` (starts on boot).
- All services use `restart: unless-stopped` - survive both container
  crashes and a full host reboot.
- `/etc/docker/daemon.json` sets a host-wide default log limit (`json-file`,
  10MB × 3 files per container) so container logs can never grow unbounded -
  applies to every project automatically, nothing to configure per-project.
- Backend runs as a non-root user (`appuser`) inside its image. The frontend
  image (`nginx:1.27-alpine`) starts its master process as root, which is
  standard/expected for stock Nginx images binding a low port - its worker
  processes (the ones actually handling requests) drop to the unprivileged
  `nginx` user internally.
- The Docker socket (`/var/run/docker.sock`) is only accessible to `root`
  and the `docker` group - not exposed to any container, not published on
  any port.
- Swap: none existed before; added a 2GB swapfile (`/swapfile`,
  `vm.swappiness=10`) as an OOM safety margin - a 2GB-RAM host running
  several small projects benefits from headroom, not from swap being used
  as a matter of course.

## Multi-project layout

```
/opt/projects/
  complaint-intake-system/   # this project - untouched, own compose.yml
  <project-2>/               # future: same pattern
  <project-3>/
/opt/backups/
  complaint-intake-system/   # timestamped, gzipped SQLite backups (see below)
  <project-2>/
/opt/ops/
  scripts/                   # backup-sqlite.sh, health-check.sh (copies of ops/scripts/ in this repo)
  logs/
```

### Adding a new project

1. `git clone <repo> /opt/projects/<name>` (as `deploy`, which owns
   `/opt/projects`).
2. Give it its own `compose.yml` and its own named volume(s) - never share a
   volume between projects.
3. **Do not** `ports: "80:80"` a second time - port 80 on the host can only
   be bound once. Two options, in order of preference as more projects
   arrive:
   - *Now (1-2 projects)*: give the new project a different host port
     (e.g. `8080:80`) if it's fine being reached at
     `http://72.56.114.70:8080/` for now, and open that port in UFW.
   - *Once there's a real reason to (a domain, or 3+ projects)*: introduce
     one shared "edge" reverse proxy (Nginx or Traefik) that alone binds
     host `:80`/`:443`, and have every project's own frontend stop
     publishing a host port at all (`expose` only), reachable from the edge
     proxy over a Docker network instead, routed by hostname
     (`server_name project2.example.com`) or path. This project's own
     Nginx config is a clean, minimal starting template for that (see
     `frontend/nginx.conf`) but the migration itself is intentionally not
     done yet - there's no second project or domain to justify it.
4. Add the new project's backup/health coverage the same way as below.

## Backups

The application database is SQLite inside the named Docker volume
`backend_data`, at `/app/data/complaint_intake.db` in the `backend`
container.

- **Script**: `ops/scripts/backup-sqlite.sh` in this repo (deployed to
  `/opt/ops/scripts/` on the server). Uses SQLite's own online-backup API
  (`sqlite3.Connection.backup()`, run inside the container via its existing
  Python) rather than copying the file directly, so a live database is
  never captured mid-write. Generic/parameterized - reusable for any future
  project's SQLite database unchanged.
- **Schedule**: daily via cron (`deploy`'s crontab), writing to
  `/opt/backups/complaint-intake-system/`, gzip-compressed, timestamped
  (`complaint-intake-system-YYYYMMDD-HHMMSS.db.gz`).
- **Retention**: 14 days, enforced by the script itself (only ever deletes
  its own project's timestamped backup files, never the live volume).
- **Restore**: stop the backend, `gunzip` the desired backup, copy it into
  the volume as `complaint_intake.db` (e.g. via `docker compose cp`), start
  the backend again. Always test a restore occasionally rather than trusting
  it blindly.
- **Off-server backups are not yet configured.** Everything above is
  *local* (same disk as the live data - protects against accidental
  deletion or a bad deploy, not against total host loss). A real
  off-server copy needs a destination this session has no credentials for
  (e.g. `rclone` to Backblaze B2 / S3 / another Timeweb Object Storage
  bucket, or Timeweb's own VPS snapshot feature from their dashboard). This
  is a manual step you'll need to do once you have an account/bucket to
  point at - `rclone` is a good lightweight fit for a 2GB host when that
  time comes.

## Monitoring / operations

Intentionally lightweight - no Prometheus/Grafana/agent stack on a 2GB
host.

- `ops/scripts/health-check.sh` (deployed to `/opt/ops/scripts/`) appends
  one line every 15 minutes (cron) to `/var/log/server-health.log`: disk
  use, memory, load average, and any container that isn't `running` or is
  reported `unhealthy`, across *all* projects on the host (not just this
  one). Rotated by `/etc/logrotate.d/server-health` (14 days, compressed).
- Manual checks: `docker compose ps` (per project), `docker stats
  --no-stream` (all containers), `htop`/`free -h`/`df -h`, `journalctl -u
  docker`, `fail2ban-client status sshd`, `ufw status verbose`.
- Timeweb's own `zabbix-agent-timeweb` (pre-installed by the provider)
  already feeds CPU/RAM/disk graphs into the Timeweb Cloud dashboard -
  that's a second, independent view that didn't require any setup here.
- No alerting/paging is configured (would need an external service -
  email/SMS/webhook credentials this session doesn't have). Checking
  `/var/log/server-health.log` and the Timeweb dashboard periodically is the
  current operational model; wiring an actual alert (e.g. a webhook to a
  chat app) is a reasonable next step once you have somewhere for it to
  notify.

## Resource limits

`compose.yml` sets conservative memory ceilings per service (`deploy.
resources.limits`, honored by plain `docker compose up` without needing
Swarm): backend 384MB (observed ~60MB in normal use), frontend 128MB
(observed ~2MB). These are a safety net against one runaway
service starving the other project(s) on the same 2GB host, not a real
constraint on this app's normal footprint - raise them if a real workload
ever needs more.

## Time

`timedatectl` showed `Etc/UTC`, NTP already synchronized
(`System clock synchronized: yes`) - left as-is; UTC is the sensible
default for a server and changing it wasn't warranted.

## HTTPS - what's left

No domain points at this VPS yet, so no TLS certificate exists and none
was requested (Let's Encrypt/Certbot needs a real domain to validate
against - fabricating one would just fail or be misleading). `certbot` and
the Nginx plugin are installed and ready so this is a short step once a
domain exists:

1. Point a domain/subdomain's DNS `A` record at `72.56.114.70`.
2. `certbot --nginx -d your-domain.example` (run inside the frontend
   container's context, or against a host-level Nginx if the shared-proxy
   migration above has happened by then) - it edits the Nginx config and
   sets up auto-renewal itself.
3. Confirm port 443 (already open in UFW) serves the new cert, and that
   HTTP redirects to HTTPS if desired.

## Regenerating this checklist after a host rebuild

If this VPS is ever rebuilt from scratch, re-apply in this order (each step
in this file is idempotent/safe to re-run): swap → fail2ban →
`/etc/docker/daemon.json` → create `deploy` user + sudoers + SSH hardening
(test the new user *before* touching root/password auth) → UFW → clone
project(s) → `ops/scripts/*` + cron + logrotate.
