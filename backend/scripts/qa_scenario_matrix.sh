#!/usr/bin/env bash
# Repeatable QA matrix: DB/tools in pytest (no mocks for SQLite/tools).
# Optional: RUN_HTTP=1 hits a live API for HTTP smoke (requires running uvicorn).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BACKEND_ROOT"
export PYTHONPATH=.
source .venv/bin/activate
pytest tests/test_qa_matrix_db.py tests/test_llm_parser.py tests/test_bytes_stt.py -q

if [[ "${RUN_HTTP:-}" == "1" ]]; then
  API="${API_BASE:-http://127.0.0.1:8000}"
  echo "== HTTP smoke against $API"
  curl -sS -m 10 -f "$API/health" >/dev/null
  code=$(curl -sS -m 60 -o /tmp/qa-http-body -w "%{http_code}" -X POST "$API/tools/invoke" \
    -H 'Content-Type: application/json' \
    -d '{"tool":"fetch_slots","arguments":{"date":"2030-06-01"}}' || true)
  [[ "$code" == "200" ]] || { echo "tools/invoke expected 200, got $code"; cat /tmp/qa-http-body; exit 1; }
  echo "HTTP smoke OK"
fi

echo "qa_scenario_matrix: OK"
