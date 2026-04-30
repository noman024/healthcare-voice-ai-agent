#!/usr/bin/env bash
# Stop processes started by start-full-stack-nohup.sh (reads logs/*.nohup.pid).
# Stops: frontend, voice-worker, musetalk, api, ollama (only if a pid file exists for each).
# LiveKit Docker is left up unless STOP_LIVEKIT_DOCKER=1. If compose was started with sudo (no socket as user),
# sudo for `down` is auto-detected; set LIVEKIT_DOCKER_SUDO=1 to force.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker_daemon_usable() {
	docker info >/dev/null 2>&1
}

stop_one() {
	local name="$1"
	local f="${ROOT}/logs/${name}.nohup.pid"
	if [[ ! -f "$f" ]]; then
		return 0
	fi
	local pid
	pid=$(cat "$f")
	if kill -0 "$pid" 2>/dev/null; then
		kill "$pid" 2>/dev/null || true
		echo "Stopped ${name} (pid ${pid})"
	else
		echo "Removed stale pid file for ${name} (${pid} not running)"
	fi
	rm -f "$f"
}

# Order: UI and worker before API / MuseTalk / Ollama they depend on.
stop_one frontend
stop_one voice-worker
stop_one musetalk
stop_one api
if [[ "${SKIP_STOP_OLLAMA:-}" != "1" ]]; then
	stop_one ollama
else
	echo "SKIP_STOP_OLLAMA=1 — left ollama running."
fi

if [[ "${STOP_LIVEKIT_DOCKER:-}" == "1" ]]; then
	cd "${ROOT}"
	if [[ "${LIVEKIT_DOCKER_SUDO:-}" != "1" ]] && ! docker_daemon_usable; then
		if sudo -n docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
			export LIVEKIT_DOCKER_SUDO=1
			echo "Note: using sudo for LiveKit docker compose down."
		fi
	fi
	_dc_down() {
		if docker compose version >/dev/null 2>&1; then
			docker compose -f docker-compose.livekit.yml down
		elif command -v docker-compose >/dev/null 2>&1; then
			docker-compose -f docker-compose.livekit.yml down
		else
			echo "Docker Compose not found; stop LiveKit manually." >&2
			return 1
		fi
	}
	if [[ "${LIVEKIT_DOCKER_SUDO:-}" == "1" ]]; then
		if docker compose version >/dev/null 2>&1; then
			if sudo -n true 2>/dev/null; then
				sudo -n docker compose -f docker-compose.livekit.yml down
			else
				sudo docker compose -f docker-compose.livekit.yml down
			fi
		else
			if sudo -n true 2>/dev/null; then
				sudo -n docker-compose -f docker-compose.livekit.yml down
			else
				sudo docker-compose -f docker-compose.livekit.yml down
			fi
		fi
	else
		_dc_down
	fi
	echo "LiveKit: docker compose down (7880)"
else
	echo "LiveKit docker container still running (use STOP_LIVEKIT_DOCKER=1 to stop it)."
fi

echo ""
echo "Note: This script does NOT stop ngrok. If you still see traffic on your tunnel URL,"
echo "      stop ngrok in its own terminal (Ctrl+C) or: pkill -f 'ngrok http 3000'"
echo "      (only if you have no other ngrok tunnels on this machine)."
