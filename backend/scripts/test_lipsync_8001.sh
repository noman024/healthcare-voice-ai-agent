#!/usr/bin/env bash
# Smoke-test POST /avatar/lipsync on the MuseTalk service (default port 8001).
# Builds a short English line via main API TTS (needs PIPER_VOICE on :8000), then uploads WAV.
set -euo pipefail
API="${MAIN_API_URL:-http://127.0.0.1:8000}"
MT="${MUSETALK_URL:-http://127.0.0.1:8001}"
TEXT="${LIPSYNC_TEST_TEXT:-Hello. This is a short English test for lip sync.}"
WAV="${TMPDIR:-/tmp}/musetalk_lipsync_test_$$.wav"
MP4="${TMPDIR:-/tmp}/musetalk_lipsync_out_$$.mp4"
export LIPSYNC_TEST_TEXT="$TEXT"

echo "GET $MT/avatar/lipsync/status"
curl -sS "$MT/avatar/lipsync/status" | python3 -m json.tool || true

echo
echo "POST $API/tts -> $WAV"
curl -sS -f -X POST "$API/tts" -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,os; print(json.dumps({"text": os.environ["LIPSYNC_TEST_TEXT"]}))')" \
  -o "$WAV"
file "$WAV"

echo
echo "POST $MT/avatar/lipsync (may take minutes on first GPU run)"
code=$(curl -sS -w "%{http_code}" -o "$MP4" -X POST "$MT/avatar/lipsync" -F "audio=@${WAV};type=audio/wav" || true)
echo "http=$code"
if [[ "$code" == 200 ]]; then
  file "$MP4"
  echo "Wrote $MP4"
else
  echo "Response body:"
  head -c 800 "$MP4" || true
  echo
  exit 1
fi
