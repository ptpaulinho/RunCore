#!/usr/bin/env bash
# Back up the SQLite database safely (online backup, no downtime) and optionally
# push it to S3. Schedule via cron, e.g. every 6h:
#   0 */6 * * * /opt/runcore/deploy/backup-db.sh >> /var/log/runcore-backup.log 2>&1
set -euo pipefail

DB="${RUNCORE_DB_PATH:-/data/cloud.db}"
OUT_DIR="${BACKUP_DIR:-/data/backups}"
S3_BUCKET="${BACKUP_S3_BUCKET:-}"   # e.g. s3://my-runcore-backups (optional)
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$OUT_DIR/cloud_${STAMP}.db"

mkdir -p "$OUT_DIR"

# .backup is a consistent online snapshot even while the app is writing.
sqlite3 "$DB" ".backup '$DEST'"
gzip -f "$DEST"
echo "backup -> ${DEST}.gz"

# Keep the 30 most recent local backups
ls -1t "$OUT_DIR"/cloud_*.db.gz 2>/dev/null | tail -n +31 | xargs -r rm -f

if [[ -n "$S3_BUCKET" ]]; then
  aws s3 cp "${DEST}.gz" "${S3_BUCKET}/" && echo "uploaded -> ${S3_BUCKET}/$(basename "${DEST}.gz")"
fi
