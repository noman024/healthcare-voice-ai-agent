#!/usr/bin/env bash
# Strict live E2E: requires API + Ollama + Piper + faster-whisper (no mocks).
# Terminal 1: optional repo Ollama: ./scripts/run_with_tools.sh ollama serve
# Terminal 2: cd backend && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000
# Ensure OLLAMA_MODEL is pulled and PIPER_* point at `.tools/piper` (see README).
set -euo pipefail
API="${API_BASE:-http://127.0.0.1:8000}"
BODY=$(mktemp)
tmpwav="$(mktemp --suffix=.wav)"
cleanup() { rm -f "$BODY" "$tmpwav"; }
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

http_get() {
  local expect="$1"
  shift
  local code
  code=$(curl -sS -m 60 -o "$BODY" -w "%{http_code}" "$@" || true)
  if [[ "$code" != "$expect" ]]; then
    echo "Expected HTTP $expect from: $*"
    head -c 400 "$BODY" || true
    echo ""
    fail "got HTTP $code"
  fi
}

http_post_json() {
  local expect="$1"
  shift
  local url="$1"
  shift
  local code
  code=$(curl -sS -m 300 -o "$BODY" -w "%{http_code}" -X POST "$url" \
    -H 'Content-Type: application/json' -d "$@" || true)
  if [[ "$code" != "$expect" ]]; then
    echo "Expected HTTP $expect POST $url"
    head -c 600 "$BODY" || true
    echo ""
    fail "got HTTP $code"
  fi
}

# ~0.5s of non-silent PCM (keeps STT path realistic vs pure zeros)
WAV_OUT="$tmpwav" python3 <<'PY'
import math
import os
import struct
import wave

path = os.environ["WAV_OUT"]
fr = 16000
n = fr // 2
with wave.open(path, "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(fr)
    frames = bytearray()
    for i in range(n):
        x = int(3000 * math.sin(2 * math.pi * 440 * i / fr))
        frames += struct.pack("<h", max(-32768, min(32767, x)))
    w.writeframes(frames)
PY

echo "== GET /health"
http_get 200 "$API/health"

echo "== GET /health/llm (Ollama must be running)"
http_get 200 "$API/health/llm"

echo "== POST /tools/invoke"
http_post_json 200 "$API/tools/invoke" \
  '{"tool":"fetch_slots","arguments":{"date":"2026-09-15"}}'

echo "== POST /stt"
code=$(curl -sS -m 300 -o "$BODY" -w "%{http_code}" \
  -X POST "$API/stt" -F "audio=@$tmpwav;type=audio/wav" || true)
if [[ "$code" != "200" ]]; then
  head -c 400 "$BODY" || true
  echo ""
  fail "/stt expected 200 got $code"
fi

echo "== POST /tts"
http_post_json 200 "$API/tts" '{"text":"E2E integration short phrase."}'

echo "== POST /agent/turn"
http_post_json 200 "$API/agent/turn" \
  '{"message":"Reply with one short friendly sentence only.","session_id":"e2e-real"}'

echo "== POST /process (text only, no WAV in response)"
http_post_json 200 "$API/process" \
  '{"message":"Say hi in one sentence.","session_id":"e2e-process","return_speech":false}'

echo "== POST /conversation (multipart text + optional TTS)"
# return_speech=true exercises Piper via pipeline
code=$(curl -sS -m 300 -o "$BODY" -w "%{http_code}" \
  -X POST "$API/conversation" \
  -F "message=Book-related small talk: acknowledge in one sentence." \
  -F "session_id=e2e-conv" \
  -F "return_speech=true" || true)
if [[ "$code" != "200" ]]; then
  head -c 600 "$BODY" || true
  echo ""
  fail "/conversation expected 200 got $code"
fi

echo "== POST /agent/summary (expects prior memory for session e2e-conv)"
http_post_json 200 "$API/agent/summary" \
  '{"session_id":"e2e-conv"}'

echo "OK — full live E2E passed against $API"
