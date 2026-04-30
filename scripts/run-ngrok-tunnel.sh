#!/usr/bin/env bash
# Expose Next.js :3000 with ngrok (HTTP + WebSocket via Next rewrites). Foreground only — Ctrl+C stops the tunnel.
# See README "Managing the nohup stack and ngrok" and §5 Client demo URL.
# One-time token: https://dashboard.ngrok.com/get-started/your-authtoken
#   ngrok config add-authtoken YOUR_TOKEN_HERE
# Or for this shell only:
#   export NGROK_AUTHTOKEN=YOUR_TOKEN_HERE
# npx will run ngrok without a global install.
set -euo pipefail
if [[ -n "${NGROK_AUTHTOKEN:-}" ]]; then
	npx --yes ngrok config add-authtoken "$NGROK_AUTHTOKEN"
fi
exec npx --yes ngrok http 3000
