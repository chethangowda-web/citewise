#!/usr/bin/env bash
# Prove the backup and restore scripts work, against the running stack, without touching live data:
# backups go to a scratch folder, restores go to scratch databases that are removed afterwards.
#
#   scripts/test_backup.sh
set -uo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

work="$(mktemp -d "${TMPDIR:-/tmp}/citewise-backup-test.XXXXXX" 2>/dev/null || mktemp -d)"
work="$(cd "$work" && pwd)"
pg() { docker compose exec -T db psql -U rag -tA "$@"; }
fail=0
check() { # check "what" <exit-code-expected> <actual>
  if [ "$2" = "$3" ]; then echo "  ok    $1"; else echo "  FAIL  $1 (expected $2, got $3)"; fail=1; fi
}
cleanup() { pg -d postgres -c "drop database if exists rag_backup_selftest with (force)" > /dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT

echo "backup"
BACKUP_DIR="$work" scripts/backup.sh > /dev/null; check "a backup is written" 0 $?
dump="$(ls -1t "$work"/citewise-*.dump | head -1)"
check "it is not empty" 1 "$([ -s "$dump" ] && echo 1 || echo 0)"

echo "restore into a throwaway database"
live="$(pg -d rag -c "select (select count(*) from users) || '/' || (select count(*) from documents) || '/' || (select count(*) from chunks)")"
out="$(scripts/restore.sh "$dump" --verify 2>&1)"; check "--verify succeeds" 0 $?
echo "$out" | grep -q "restores cleanly" && echo "  ok    it reports what is inside ($out)" || { echo "  FAIL  no summary"; fail=1; }
check "no scratch database left behind" 0 "$(pg -d postgres -c "select count(*) from pg_database where datname like 'restore_check%'")"

echo "bad input"
head -c "$(( $(wc -c < "$dump") / 2 ))" "$dump" > "$work/damaged.dump"
scripts/restore.sh "$work/damaged.dump" --verify > /dev/null 2>&1; check "a damaged backup is refused" 1 $?
scripts/restore.sh "$dump" --replace > /dev/null 2>&1; check "--replace needs --yes" 2 $?
scripts/restore.sh "$work/missing.dump" --verify > /dev/null 2>&1; check "a missing file is refused" 2 $?
DB_NAME=no_such_database BACKUP_DIR="$work/x" scripts/backup.sh > /dev/null 2>&1; check "backing up a missing database fails" 1 $?
check "and leaves no half-written file" 0 "$(ls "$work/x" 2>/dev/null | wc -l | tr -d ' ')"

echo "replace (on a scratch database, never the live one)"
pg -d postgres -c "drop database if exists rag_backup_selftest with (force)" -c "create database rag_backup_selftest" > /dev/null
pg -d rag_backup_selftest -c "create table junk(x int)" > /dev/null
DB_NAME=rag_backup_selftest STOP_SERVICES="" scripts/restore.sh "$dump" --replace --yes > /dev/null 2>&1; check "--replace succeeds" 0 $?
check "what was there before is gone" 0 "$(pg -d rag_backup_selftest -c "select count(*) from information_schema.tables where table_name='junk'")"
copy="$(pg -d rag_backup_selftest -c "select (select count(*) from users) || '/' || (select count(*) from documents) || '/' || (select count(*) from chunks)")"
check "it now holds the backed-up data" "$live" "$copy"

echo "rotation"
for _ in 1 2 3; do KEEP=2 BACKUP_DIR="$work/rot" scripts/backup.sh > /dev/null; sleep 1; done
check "only the newest 2 are kept" 2 "$(ls "$work"/rot/citewise-*.dump | wc -l | tr -d ' ')"

[ "$fail" = 0 ] && echo "all backup checks passed" || { echo "SOME CHECKS FAILED"; exit 1; }
