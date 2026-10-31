#!/usr/bin/env bash
# Back up the Citewise database (accounts, documents and their passages, conversations, the audit ledger).
#
#   scripts/backup.sh                 # writes backups/citewise-YYYYMMDD-HHMMSS.dump
#   KEEP=30 scripts/backup.sh         # keep the newest 30 (default 14)
#   BACKUP_DIR=/mnt/offsite scripts/backup.sh
#
# A backup that cannot be read back is worse than none, so the archive is checked before it is kept, and a
# failed one is never left behind. Backups hold password hashes and every conversation: treat the folder as
# sensitive (the files are created readable by you only), copy them off this machine, and encrypt them there.
#
# Schedule it: cron  `15 3 * * *  cd /path/to/policy-rag && scripts/backup.sh`
#              Windows Task Scheduler: run  bash scripts/backup.sh  in the project folder, daily.
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-backups}"
KEEP="${KEEP:-14}"
DB_USER="${DB_USER:-rag}"
DB_NAME="${DB_NAME:-rag}"
DB_SERVICE="${DB_SERVICE:-db}"

mkdir -p "$BACKUP_DIR"
umask 077
stamp="$(date +%Y%m%d-%H%M%S)"
out="$BACKUP_DIR/citewise-$stamp.dump"
tmp="$out.partial"
trap 'rm -f "$tmp"' EXIT

docker compose exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$tmp"

if [ ! -s "$tmp" ]; then
  echo "backup failed: the dump is empty" >&2
  exit 1
fi
if ! docker compose exec -T "$DB_SERVICE" pg_restore --list < "$tmp" > /dev/null 2>&1; then
  echo "backup failed: the dump is not a readable archive" >&2
  exit 1
fi
mv "$tmp" "$out"

# keep only the newest $KEEP backups
ls -1t "$BACKUP_DIR"/citewise-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f -- "$old"; done

echo "backup ok: $out ($(wc -c < "$out") bytes; keeping the newest $KEEP)"
