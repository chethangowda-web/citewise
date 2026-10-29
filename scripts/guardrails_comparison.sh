#!/usr/bin/env bash
# Measure what the guardrails actually prevent: run the same 44-question golden set against two
# scratch backends that use the SAME model, one with guardrails on and one with them off.
#
#   scripts/guardrails_comparison.sh            # full run (spends LLM quota: ~28k + ~55k tokens)
#   scripts/guardrails_comparison.sh --dry-run  # set everything up, run nothing, clean up
#
# Resumable: if the provider's quota runs out, the run pauses (exit code 3) and keeps its progress in
# backend/eval/*.partial. Re-run the same command later and it continues without re-asking.
# Uses a separate database (rag_eval), so your real audit log and documents are untouched.
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
PY=backend/.venv/Scripts/python; [ -x "$PY" ] || PY=backend/.venv/bin/python; [ -x "$PY" ] || PY=python3
EVAL=backend/eval
DB_URL="postgresql+psycopg://rag:${POSTGRES_PASSWORD:-rag}@db:5432/rag_eval"
COMMON=(--no-deps -e RATE_LIMIT_PER_MINUTE=0 -e "DATABASE_URL=$DB_URL")

cleanup() { docker rm -f rag-eval-off rag-eval-on >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

echo "== preparing scratch database and backends"
docker compose up -d db >/dev/null
for _ in $(seq 1 30); do docker compose exec -T db pg_isready -U rag >/dev/null 2>&1 && break; sleep 1; done
docker compose exec -T db psql -U rag -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='rag_eval'" | grep -q 1 \
  || docker compose exec -T db psql -U rag -d postgres -c "CREATE DATABASE rag_eval" >/dev/null

docker compose run -d "${COMMON[@]}" --name rag-eval-off -p 8002:8000 -e GUARDRAILS_ENABLED=false backend >/dev/null
docker compose run -d "${COMMON[@]}" --name rag-eval-on  -p 8003:8000 -e GUARDRAILS_ENABLED=true  backend >/dev/null
for port in 8002 8003; do
  for _ in $(seq 1 40); do curl -fs -m 3 "localhost:$port/api/health" >/dev/null 2>&1 && break; sleep 2; done
  curl -fs -m 3 "localhost:$port/api/health" >/dev/null || { echo "backend on :$port did not become healthy"; exit 1; }
done
docker exec rag-eval-on python -m app.rag.ingest /data/policies | tail -1
echo "   guardrails-off on :8002, guardrails-on on :8003, same model: $(grep -E '^LLM_PROVIDER=' .env | cut -d= -f2)"

if [ "$DRY" = 1 ]; then echo "dry run complete: nothing was sent to the LLM"; exit 0; fi

run() {  # label port outfile
  echo "== evaluating $1 (port $2)"
  set +e
  "$PY" -u "$EVAL/run.py" --base-url "http://localhost:$2" --label "$1" --no-gate --delay 6 --out "$3"
  rc=$?
  set -e
  if [ $rc -eq 3 ]; then echo; echo "Paused: provider quota or outage. Re-run this script later to continue."; exit 3; fi
  [ $rc -eq 0 ] || exit $rc
}
run guardrails-on  8003 "$EVAL/report_on.json"
run guardrails-off 8002 "$EVAL/report_off.json"

"$PY" "$EVAL/compare.py" "$EVAL/report_off.json" "$EVAL/report_on.json" | tee "$EVAL/COMPARE.md"
echo "wrote $EVAL/COMPARE.md"
