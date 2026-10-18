#!/bin/sh
# Back up the database and workspace/ from the running compose stack.
#
#   scripts/backup.sh                 # writes to ./backups, keeps 14 days
#   BACKUP_DIR=/Volumes/Backup/repgen BACKUP_KEEP_DAYS=30 scripts/backup.sh
#
# Copy the results off this machine: set BACKUP_SYNC_COMMAND, for example
#   BACKUP_SYNC_COMMAND='rclone sync ./backups r2:repgen-backups'
# Never store the encryption master key (Phase 4) next to these files.
set -eu

cd "$(dirname "$0")/.."
DEST="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

# Custom-format dump: compressed, restorable table by table with pg_restore.
docker compose exec -T postgres pg_dump -U repgen -d repgen --format=custom > "$DEST/db-$STAMP.dump.partial"
mv "$DEST/db-$STAMP.dump.partial" "$DEST/db-$STAMP.dump"

tar -czf "$DEST/workspace-$STAMP.tar.gz.partial" workspace
mv "$DEST/workspace-$STAMP.tar.gz.partial" "$DEST/workspace-$STAMP.tar.gz"

find "$DEST" -name 'db-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name 'workspace-*.tar.gz' -mtime +"$KEEP_DAYS" -delete

if [ -n "${BACKUP_SYNC_COMMAND:-}" ]; then
    sh -c "$BACKUP_SYNC_COMMAND"
fi
echo "Backup written: $DEST/db-$STAMP.dump, $DEST/workspace-$STAMP.tar.gz"
