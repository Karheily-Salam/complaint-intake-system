#!/usr/bin/env bash
# Lightweight periodic health snapshot for a small (2GB RAM) VPS host - no
# heavy monitoring stack, no daemon, just a cron-driven line appended to a
# log file. Covers disk, memory, load, and every running container's
# status/healthcheck across ALL projects on the host (not just one).
#
# The log is rotated by /etc/logrotate.d/server-health so it never grows
# unbounded.

set -uo pipefail

LOG_FILE="${LOG_FILE:-/var/log/server-health.log}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

DISK_USE="$(df -h / | awk 'NR==2 {print $5}')"
MEM_LINE="$(free -m | awk 'NR==2 {printf "%dMi/%dMi", $3, $2}')"
LOAD="$(cut -d' ' -f1-3 /proc/loadavg)"

UNHEALTHY=""
for cid in $(docker ps -q 2>/dev/null); do
  name="$(docker inspect --format '{{.Name}}' "$cid" | sed 's#^/##')"
  status="$(docker inspect --format '{{.State.Status}}' "$cid")"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' "$cid")"
  if [ "$status" != "running" ] || [ "$health" = "unhealthy" ]; then
    UNHEALTHY="${UNHEALTHY}${name}:${status}/${health} "
  fi
done

if [ -n "$UNHEALTHY" ]; then
  echo "${TIMESTAMP} ALERT disk=${DISK_USE} mem=${MEM_LINE} load=${LOAD} unhealthy=[${UNHEALTHY}]" >> "$LOG_FILE"
else
  echo "${TIMESTAMP} ok disk=${DISK_USE} mem=${MEM_LINE} load=${LOAD}" >> "$LOG_FILE"
fi
