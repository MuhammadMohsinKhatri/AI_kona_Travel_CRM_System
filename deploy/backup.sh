#!/usr/bin/env bash
#
# Nightly Postgres backup for the Kona Ice automation system.
#
# Installed as a root cron entry — see deploy/BACKUPS.md. Writes a compressed
# dump per night, keeps 14 of them plus one per month indefinitely, and (when
# configured) copies each one offsite.
#
# Exits non-zero on any failure so cron mails the error rather than silently
# producing nothing. A backup that fails quietly is worse than none, because it
# is believed.
set -euo pipefail

STACK_DIR="${STACK_DIR:-/opt/konaice}"
COMPOSE_FILE="$STACK_DIR/docker-compose.prod.yml"
BACKUP_DIR="${BACKUP_DIR:-$STACK_DIR/backups}"
DAILY_KEEP_DAYS="${DAILY_KEEP_DAYS:-14}"

# Optional offsite copy. Set RCLONE_REMOTE to something like "b2:konaice-backups"
# in /etc/default/konaice-backup. Left empty, backups stay on this server only —
# which protects against a bad deploy or a dropped table, but NOT against losing
# the server or the Hostinger account. See BACKUPS.md.
RCLONE_REMOTE="${RCLONE_REMOTE:-}"

STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
DAY_OF_MONTH="$(date -u +%d)"
OUT="$BACKUP_DIR/konaice-$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date -u +%FT%TZ)] backing up to $OUT"

# -T: no TTY. Without it this works by hand and fails under cron, which is the
# classic way a backup job is "installed" and never actually runs.
# The pipeline is checked as a whole (pipefail) so a pg_dump failure cannot be
# hidden by gzip exiting 0 on a truncated stream.
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U konaice --clean --if-exists konaice \
    | gzip -9 > "$OUT.partial"

# Only becomes a real backup once it is complete. A half-written file with the
# right name is what you find at 2am during a restore.
mv "$OUT.partial" "$OUT"

SIZE="$(du -h "$OUT" | cut -f1)"
echo "[$(date -u +%FT%TZ)] wrote $OUT ($SIZE)"

# A dump far smaller than yesterday's means something is wrong — an empty
# database, a permissions change, a container that came up without its volume.
# Louder than a silent success.
if [ "$(stat -c%s "$OUT")" -lt 10240 ]; then
    echo "WARNING: backup is under 10 KB — is the database empty?" >&2
fi

if [ -n "$RCLONE_REMOTE" ]; then
    echo "[$(date -u +%FT%TZ)] copying offsite to $RCLONE_REMOTE"
    rclone copy "$OUT" "$RCLONE_REMOTE" --no-traverse
fi

# Retention. The 1st of each month is kept forever: a table quietly corrupted in
# March is not always noticed by April, and 14 days of history cannot reach back
# past a problem nobody spotted.
if [ "$DAY_OF_MONTH" != "01" ]; then
    find "$BACKUP_DIR" -name 'konaice-*.sql.gz' -mtime "+$DAILY_KEEP_DAYS" \
        ! -name 'konaice-*-01T*' -delete
fi

echo "[$(date -u +%FT%TZ)] done. $(ls -1 "$BACKUP_DIR"/konaice-*.sql.gz | wc -l) backups on disk"
