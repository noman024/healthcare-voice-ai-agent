#!/usr/bin/env bash
# End-to-end smoke against a running API (default http://127.0.0.1:8000).
# Terminal 1 (backend): cd backend && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000
# Optional: `./scripts/run_with_tools.sh ollama serve` + `ollama pull $OLLAMA_MODEL`; Piper + PIPER_VOICE for /tts WAV.
set -euo pipefail
API="${API_BASE:-http://127.0.0.1:8000}"
fail=0

tmpwav="$(mktemp --suffix=.wav)"
cleanup() { rm -f "$tmpwav"; }
trap cleanup EXIT

WAV_OUT="$tmpwav" python3 <<'PY'
import os
import wave

p = os.environ["WAV_OUT"]
with wave.open(p, "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 8000)
PY

echo "== GET /health"
curl -sS -f "$API/health"
echo ""

echo "== GET /health/llm (200=Ollama up, 503=down)"
code_llm=$(curl -sS -o /tmp/llm.json -w "%{http_code}" "$API/health/llm" || true)
cat /tmp/llm.json
echo ""
if [[ "$code_llm" != "200" && "$code_llm" != "503" ]]; then echo "unexpected llm health: $code_llm"; fail=1; fi

echo "== POST /tools/invoke fetch_slots"
curl -sS -f -X POST "$API/tools/invoke" \
  -H 'Content-Type: application/json' \
  -d '{"tool":"fetch_slots","arguments":{"date":"2026-09-15"}}'
echo ""

echo "== POST /stt (real faster-whisper; set WHISPER_MODEL in backend env)"
curl -sS -f -X POST "$API/stt" -F "audio=@$tmpwav;type=audio/wav"
echo ""

echo "== POST /tts (503 if PIPER_VOICE unset)"
code_tts=$(curl -sS -o /tmp/tts.bin -w "%{http_code}" \
  -X POST "$API/tts" -H 'Content-Type: application/json' -d '{"text":"E2E test phrase"}' || true)
if [[ "$code_tts" == "200" ]]; then echo "TTS WAV bytes: $(wc -c </tmp/tts.bin)";
elif [[ "$code_tts" == "503" ]]; then echo "TTS 503 — set PIPER_VOICE and install piper";
else echo "TTS unexpected $code_tts"; head -c 200 /tmp/tts.bin; echo ""; fail=1; fi

echo "== POST /agent/turn (200 with Ollama; 502 if Ollama down)"
code_agent=$(curl -sS -m 180 -o /tmp/agent.json -w "%{http_code}" \
  -X POST "$API/agent/turn" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Say hello briefly.","session_id":"e2e-smoke"}' || true)
head -c 1200 /tmp/agent.json
echo ""
if [[ "$code_agent" == "200" ]]; then echo "Agent OK";
elif [[ "$code_agent" == "502" || "$code_agent" == "504" ]]; then echo "NOTE: Agent needs Ollama (HTTP $code_agent) — not counted as failure.";
elif [[ "$code_agent" == "422" ]]; then echo "Agent returned 422 (bad JSON from model) — check OLLAMA_MODEL"; fail=1;
else echo "unexpected agent: $code_agent"; fail=1; fi

exit "$fail"
