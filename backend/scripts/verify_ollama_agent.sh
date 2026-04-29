#!/usr/bin/env bash
# Manual check: Ollama up + one /agent/turn against a running API.
# Usage: from repo root, with backend venv active and uvicorn on :8000 —
#   OLLAMA_MODEL=qwen2.5:7b-instruct ./backend/scripts/verify_ollama_agent.sh
set -euo pipefail
OLLAMA="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
API="${API_BASE:-http://127.0.0.1:8000}"
echo "== Ollama: GET ${OLLAMA}/api/tags"
curl -sS -m 5 "${OLLAMA}/api/tags" | head -c 400 || {
  echo "FAIL: Ollama not reachable. Start with: ./scripts/run_with_tools.sh ollama serve"
  exit 1
}
echo ""
echo "== Backend: POST ${API}/agent/turn"
curl -sS -m 180 -X POST "${API}/agent/turn" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Say hello in one short sentence.","session_id":"verify-ollama"}' \
  | head -c 1200
echo ""
