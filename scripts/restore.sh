#!/usr/bin/env bash
# Restore a Citewise backup made by scripts/backup.sh.
#
#   scripts/restore.sh FILE.dump --verify          # prove the backup restores: load it into a throwaway
#                                                  # database, print what is in it, delete it. Touches nothing live.
#   scripts/restore.sh FILE.dump --replace --yes   # REPLACE the live database with the backup
#
# --replace stops the backend, drops the database, restores the file and starts the backend again. Everything
# written since the backup is lost, so take a fresh backup first if there is anything worth keeping:
#   scripts/backup.sh && scripts/restore.sh backups/citewise-....dump --replace --yes
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

DB_USER="${DB_USER:-rag}"
DB_NAME="${DB_NAME:-rag}"
DB_SERVICE="${DB_SERVICE:-db}"
STOP_SERVICES="${STOP_SERVICES-backend}"   # set to an empty string to leave the app running (used by the tests)
file="${1:-}"
mode="${2:-}"

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ -n "$file" ] && [ -f "$file" ] || { echo "backup file not found: ${file:-<none>}" >&2; usage; }
case "$mode" in --verify|--replace) ;; *) usage ;; esac
if [ "$mode" = "--replace" ] && [ "${3:-}" != "--yes" ]; then
  echo "This replaces the database '$DB_NAME' with $file and loses anything newer." >&2
  echo "Run again with --yes to go ahead." >&2
  exit 2
fi

psql_db() { docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -v ON_ERROR_STOP=1 -tA "$@"; }
restore_into() { docker compose exec -T "$DB_SERVICE" pg_restore -U "$DB_USER" -d "$1" --no-owner --exit-on-error < "$file"; }
summary() {
  psql_db -d "$1" -c "select 'users=' || (select count(*) from users) || ' documents=' || (select count(*) from documents) ||
    ' passages=' || (select count(*) from chunks) || ' conversations=' || (select count(*) from chat_sessions) ||
    ' audit_rows=' || (select count(*) from audit_logs)"
}

if [ "$mode" = "--verify" ]; then
  scratch="restore_check_$(date +%s)"
  trap 'psql_db -d postgres -c "drop database if exists \"'"$scratch"'\" with (force)" > /dev/null 2>&1 || true' EXIT
  psql_db -d postgres -c "create database \"$scratch\"" > /dev/null
  restore_into "$scratch"
  echo "restores cleanly. It contains: $(summary "$scratch")"
  exit 0
fi

# --replace
for svc in $STOP_SERVICES; do docker compose stop "$svc" > /dev/null; done
psql_db -d postgres -c "drop database if exists \"$DB_NAME\" with (force)" > /dev/null
psql_db -d postgres -c "create database \"$DB_NAME\"" > /dev/null
restore_into "$DB_NAME"
for svc in $STOP_SERVICES; do docker compose start "$svc" > /dev/null; done
echo "restored $file into '$DB_NAME'. It contains: $(summary "$DB_NAME")"
