#!/usr/bin/env bash
# Expose Next.js :3000 with ngrok (HTTP + WebSocket via Next rewrites).
# Default: nohup in the background → logs/ngrok.nohup.log, logs/ngrok.nohup.pid (matches start-full-stack-nohup.sh).
# Foreground (Ctrl+C stops): ./run-ngrok-tunnel.sh --foreground  or  NGROK_FOREGROUND=1
# See README "Managing the nohup stack and ngrok" and §5 Client demo URL.
# One-time token: https://dashboard.ngrok.com/get-started/your-authtoken
#   ngrok config add-authtoken YOUR_TOKEN_HERE
# Or for this shell only:
#   export NGROK_AUTHTOKEN=YOUR_TOKEN_HERE
# npx will run ngrok without a global install.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${ROOT}/logs"

if [[ -n "${NGROK_AUTHTOKEN:-}" ]]; then
	npx --yes ngrok config add-authtoken "$NGROK_AUTHTOKEN"
fi

foreground=0
if [[ "${NGROK_FOREGROUND:-0}" != "0" ]] || [[ "${1:-}" == "--foreground" ]]; then
	foreground=1
fi

if [[ "$foreground" -eq 1 ]]; then
	exec npx --yes ngrok http 3000
fi

nohup npx --yes ngrok http 3000 >>"${ROOT}/logs/ngrok.nohup.log" 2>&1 &
echo $! >"${ROOT}/logs/ngrok.nohup.pid"
echo "Started ngrok pid $(cat "${ROOT}/logs/ngrok.nohup.pid") (log: ${ROOT}/logs/ngrok.nohup.log)"
echo "Tunnel UI / JSON: http://127.0.0.1:4040  |  curl -s http://127.0.0.1:4040/api/tunnels"
