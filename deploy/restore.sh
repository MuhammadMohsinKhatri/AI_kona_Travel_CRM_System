#!/usr/bin/env bash
#
# Restore the Kona Ice database from a backup produced by deploy/backup.sh.
#
#   ./restore.sh /opt/konaice/backups/konaice-2026-08-03T020000Z.sql.gz
#
# Destructive by design: the dump is taken with --clean --if-exists, so it drops
# and recreates every table it restores. Anything written since that backup is
# gone. The confirmation prompt is deliberate — this is the one script here that
# can lose data, and it should be hard to run by accident.
set -euo pipefail

STACK_DIR="${STACK_DIR:-/opt/konaice}"
COMPOSE_FILE="$STACK_DIR/docker-compose.prod.yml"
DUMP="${1:-}"

if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    echo "usage: $0 <backup.sql.gz>" >&2
    echo >&2
    echo "available:" >&2
    ls -lh "$STACK_DIR"/backups/konaice-*.sql.gz 2>/dev/null >&2 || echo "  (none)" >&2
    exit 1
fi

echo "About to restore:  $DUMP"
echo "Taken:             $(stat -c%y "$DUMP")"
echo "Size:              $(du -h "$DUMP" | cut -f1)"
echo
echo "This REPLACES the current database. Everything recorded since that"
echo "backup — events, invoices, payments — will be lost."
echo
read -r -p "Type the word RESTORE to continue: " CONFIRM
[ "$CONFIRM" = "RESTORE" ] || { echo "Aborted."; exit 1; }

# Take a safety copy first. If the dump turns out to be the wrong one or is
# itself damaged, the state you just replaced is still recoverable — which is
# not true of the alternative.
SAFETY="$STACK_DIR/backups/pre-restore-$(date -u +%Y-%m-%dT%H%M%SZ).sql.gz"
echo "Saving current state to $SAFETY first…"
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U konaice --clean --if-exists konaice | gzip -9 > "$SAFETY"

# Stop the writers. Restoring underneath a running pipeline gives you a database
# that is half old and half new, which is worse than either.
echo "Stopping application services…"
docker compose -f "$COMPOSE_FILE" stop backend worker beat

echo "Restoring…"
gunzip -c "$DUMP" | docker compose -f "$COMPOSE_FILE" exec -T db psql -U konaice -d konaice

echo "Starting application services…"
docker compose -f "$COMPOSE_FILE" start backend worker beat

echo
echo "Done. Check the dashboard, then verify:"
echo "  curl -s localhost/health"
echo "  docker compose -f $COMPOSE_FILE logs --tail=50 backend"
echo
echo "If this restored the wrong thing, the state from before is at:"
echo "  $SAFETY"
