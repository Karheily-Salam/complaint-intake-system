#!/usr/bin/env bash
# Safely back up a SQLite database that lives inside a Docker named volume.
#
# Uses SQLite's own online backup API (via the target container's Python,
# which already has the sqlite3 stdlib module) instead of copying the file
# directly - a plain `cp`/`docker cp` of a live SQLite file can capture a
# torn, inconsistent snapshot mid-write. sqlite3.Connection.backup() is the
# correct, safe way to copy a database that may be open elsewhere.
#
# Generic on purpose: any current or future project that uses a SQLite file
# inside a compose service can reuse this unchanged.
#
# Usage:
#   backup-sqlite.sh <compose-project-dir> <service-name> <db-path-in-container> <backup-name>
#
# Example (complaint-intake-system, run from anywhere):
#   backup-sqlite.sh /opt/projects/complaint-intake-system backend \
#     /app/data/complaint_intake.db complaint-intake-system
#
# Env vars:
#   BACKUP_ROOT     - base directory for all projects' backups (default /opt/backups)
#   RETENTION_DAYS  - delete this project's backups older than N days (default 14)

set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 <compose-project-dir> <service-name> <db-path-in-container> <backup-name>" >&2
  exit 2
fi

PROJECT_DIR="$1"
SERVICE="$2"
DB_PATH="$3"
NAME="$4"

BACKUP_ROOT="${BACKUP_ROOT:-/opt/backups}/${NAME}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
TMP_IN_CONTAINER="/tmp/backup-${TIMESTAMP}.db"
DEST="${BACKUP_ROOT}/${NAME}-${TIMESTAMP}.db"

mkdir -p "$BACKUP_ROOT"

cd "$PROJECT_DIR"

docker compose exec -T "$SERVICE" python -c "
import sqlite3
src = sqlite3.connect('${DB_PATH}')
dst = sqlite3.connect('${TMP_IN_CONTAINER}')
src.backup(dst)
dst.close()
src.close()
"

docker compose cp "${SERVICE}:${TMP_IN_CONTAINER}" "$DEST"
docker compose exec -T "$SERVICE" rm -f "$TMP_IN_CONTAINER"

gzip -f "$DEST"
echo "Backup written: ${DEST}.gz"

# Retention: only ever deletes this project's own timestamped backups, and
# only ones older than RETENTION_DAYS - never touches the live application
# volume or database.
find "$BACKUP_ROOT" -maxdepth 1 -name "${NAME}-*.db.gz" -mtime "+${RETENTION_DAYS}" -print -delete
