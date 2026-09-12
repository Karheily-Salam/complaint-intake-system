🇬🇧 **English** | [🇷🇺 Русский](backup-restore.ru.md)

# Backup and restore drill

"We have backups" and "we have backups that restore" are different claims. This
page documents the second one, and the drill below has been performed rather
than only written down.

## What is backed up

The application database: SQLite at `/app/data/complaint_intake.db` inside the
`backend` container, persisted in the named Docker volume `backend_data`. It
holds every conversation, message, complaint, collected field and ticket.

Not backed up, because it is reproducible or must not be copied around:

| Not in backups | Why |
|---|---|
| `backend/.env` | Contains the mailbox password and staff key. Kept only on the server, mode `600`. |
| Docker images | Rebuilt from the repository. |
| The mailbox itself | Owned by the mail provider. Processed mail stays there, marked `\Seen`. |

## How backups are taken

`ops/scripts/backup-sqlite.sh`, run daily at 03:30 by the `deploy` user's
crontab.

It uses SQLite's **online backup API** (`sqlite3.Connection.backup()`), executed
inside the container through the Python already present, and *not*
`cp` or `docker cp`, either of which can capture a torn file while a write is
in progress.

- **Location:** `/opt/backups/complaint-intake-system/`
- **Naming:** `complaint-intake-system-YYYYMMDD-HHMMSS.db.gz`
- **Retention:** 14 days, enforced by the script, which only ever deletes its
  own timestamped files, never the live volume.

```bash
# Take one by hand
/opt/ops/scripts/backup-sqlite.sh /opt/projects/complaint-intake-system \
    backend /app/data/complaint_intake.db complaint-intake-system
```

**Off-server copies do not exist yet.** Everything above lives on the same disk
as the live data, which protects against a bad deploy or accidental deletion,
not against losing the host. That gap is real and stated here rather than
implied away.

## The drill

Restore into an isolated location and verify it there. Nothing below touches
the live database or the running containers.

### 1. Restore a backup to a temporary file

```bash
cd /tmp && mkdir -p restore-drill && cd restore-drill
cp /opt/backups/complaint-intake-system/complaint-intake-system-<TIMESTAMP>.db.gz .
gunzip complaint-intake-system-<TIMESTAMP>.db.gz
mv complaint-intake-system-<TIMESTAMP>.db restored.db
```

### 2. Verify the file is a sound database

```bash
sqlite3 restored.db "PRAGMA integrity_check;"     # expect: ok
sqlite3 restored.db "PRAGMA foreign_key_check;"   # expect: no output
sqlite3 restored.db "SELECT COUNT(*) FROM tickets;"
sqlite3 restored.db "SELECT version_num FROM alembic_version;"
```

The `alembic_version` value matters: it tells you which schema the backup is
at, and therefore whether the current code can run against it.

### 3. Verify the application actually works against it

Integrity is not compatibility: a sound file at an old schema still breaks.
Start a throwaway container pointed at the restored copy:

```bash
docker run --rm -p 127.0.0.1:8099:8000 \
  -v /tmp/restore-drill:/restore \
  -e DATABASE_URL=sqlite:////restore/restored.db \
  -e AI_PROVIDER=rule_based -e EMAIL_PROVIDER=mock \
  -e STAFF_API_KEY=drill-only-key \
  complaint-intake-backend:latest

curl -fsS http://127.0.0.1:8099/api/v1/health
curl -fsS -H 'X-API-Key: drill-only-key' http://127.0.0.1:8099/api/v1/ops/stats
```

Startup runs pending migrations, so this also proves the backup can be brought
up to the current schema.

### 4. Clean up

```bash
rm -rf /tmp/restore-drill
```

## Restoring for real

Only when the live database is actually lost or corrupt:

```bash
cd /opt/projects/complaint-intake-system
docker compose stop backend                      # stop writes first
gunzip -c /opt/backups/.../<file>.db.gz > /tmp/restored.db
docker compose cp /tmp/restored.db backend:/app/data/complaint_intake.db
docker compose start backend
docker compose logs -f backend                   # migrations then "startup complete"
```

Take a backup of the *current* (broken) file first if there is any chance it
still holds data the backup does not.

## Last verified

The drill was executed against a seeded database on 2026-09-10:

```
integrity_check:   ok
foreign_key_check: no violations
tickets:           000001 … 000005
alembic revision:  c4e7a91b2f68
application:       health ok, 5 tickets served from the restored file
```

Re-run it after any schema change that alters how data is stored, and at least
whenever the backup script itself changes.
