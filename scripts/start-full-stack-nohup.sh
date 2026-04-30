#!/usr/bin/env bash
# Full stack under nohup: LiveKit (Docker), Ollama (LLM), FastAPI :8000, MuseTalk :8001 (optional),
# LiveKit voice worker, Next :3000.
#
# Stop: scripts/stop-full-stack-nohup.sh (+ optional STOP_LIVEKIT_DOCKER=1). README: "Managing the nohup stack and ngrok".
#
# Environment (all optional):
#   SKIP_OLLAMA=1              — do not start `ollama serve` (use systemd or manual).
#   SKIP_MUSETALK=1            — skip MuseTalk :8001.
#   SKIP_LIVEKIT_DOCKER=1      — skip LiveKit compose.
#   LIVEKIT_DOCKER_SUDO=1      — force docker compose with sudo (otherwise auto-tries sudo if docker.sock denies current user).
#   OLLAMA_LISTEN_URL=         — health probe URL (default http://127.0.0.1:11434).
#   OLLAMA_AUTO_PULL=1        — after Ollama is up, `ollama pull` the model from backend/.env OLLAMA_MODEL (can take a long time).
set -euo pipefail

docker_daemon_usable() {
	docker info >/dev/null 2>&1
}

# True if root or docker group can use daemon, OR sudo can run docker info.
docker_usable_for_livekit() {
	docker_daemon_usable && return 0
	sudo -n docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1
}

# Prefix docker / docker-compose with sudo when LIVEKIT_DOCKER_SUDO=1 (user cannot access docker.sock).
_docker_for_livekit() {
	if [[ "${LIVEKIT_DOCKER_SUDO:-}" == "1" ]]; then
		if sudo -n true 2>/dev/null; then
			sudo -n "$@"
		else
			sudo "$@"
		fi
	else
		"$@"
	fi
}

livekit_compose_up() {
	local compose_file="$1"
	if _docker_for_livekit docker compose version >/dev/null 2>&1; then
		_docker_for_livekit docker compose -f "$compose_file" up -d
	elif command -v docker-compose >/dev/null 2>&1; then
		_docker_for_livekit docker-compose -f "$compose_file" up -d
	else
		echo "Need Docker Compose: install the 'docker compose' V2 plugin or the docker-compose package." >&2
		exit 1
	fi
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${ROOT}/logs"

OLLAMA_LISTEN_URL="${OLLAMA_LISTEN_URL:-http://127.0.0.1:11434}"

ollama_ready() {
	curl -sf --max-time 2 "${OLLAMA_LISTEN_URL}/api/tags" >/dev/null 2>&1
}

start_ollama_if_needed() {
	if [[ "${SKIP_OLLAMA:-}" == "1" ]]; then
		echo "SKIP_OLLAMA=1 — not starting ollama serve (ensure ${OLLAMA_LISTEN_URL} answers /api/tags)."
		return 0
	fi
	if ollama_ready; then
		echo "Ollama already running (${OLLAMA_LISTEN_URL})."
		return 0
	fi
	if ! command -v ollama >/dev/null 2>&1; then
		echo "WARN: ollama not on PATH and ${OLLAMA_LISTEN_URL} is not up — install Ollama, fix PATH, or set SKIP_OLLAMA=1." >&2
		return 0
	fi
	nohup ollama serve >>"${ROOT}/logs/ollama.nohup.log" 2>&1 &
	echo $! >"${ROOT}/logs/ollama.nohup.pid"
	echo "Started ollama serve pid $(cat "${ROOT}/logs/ollama.nohup.pid") (log: logs/ollama.nohup.log)"
	local waited=0
	while [[ "$waited" -lt 20 ]] && ! ollama_ready; do
		sleep 1
		waited=$((waited + 1))
	done
	if ollama_ready; then
		echo "Ollama is ready (${OLLAMA_LISTEN_URL})."
	else
		echo "WARN: Ollama not responding after ~${waited}s — see logs/ollama.nohup.log" >&2
	fi
}

ollama_pull_model_from_env() {
	if [[ "${OLLAMA_AUTO_PULL:-}" != "1" ]]; then
		return 0
	fi
	local env_file="${ROOT}/backend/.env"
	if [[ ! -f "$env_file" ]]; then
		echo "OLLAMA_AUTO_PULL=1 but backend/.env missing — skip pull." >&2
		return 0
	fi
	local model
	model="$(grep -E '^[[:space:]]*OLLAMA_MODEL=' "$env_file" | head -1 | cut -d= -f2-)"
	model="${model%%#*}"
	model="${model//\"/}"
	model="${model//\'/}"
	model="${model%"${model##*[![:space:]]}"}"
	model="${model#"${model%%[![:space:]]*}"}"
	if [[ -z "$model" ]]; then
		echo "OLLAMA_AUTO_PULL=1 but OLLAMA_MODEL not found in backend/.env — skip pull." >&2
		return 0
	fi
	echo "ollama pull ${model} (logging to logs/ollama-pull.log) …"
	if ollama pull "$model" >>"${ROOT}/logs/ollama-pull.log" 2>&1; then
		echo "ollama pull ${model} — done."
	else
		echo "WARN: ollama pull ${model} failed — see logs/ollama-pull.log" >&2
	fi
}

cd "${ROOT}"
if [[ "${SKIP_LIVEKIT_DOCKER:-}" == "1" ]]; then
	echo "SKIP_LIVEKIT_DOCKER=1 — not running docker compose (ensure LiveKit is already up on 7880)."
else
	if [[ "${LIVEKIT_DOCKER_SUDO:-}" != "1" ]] && ! docker_daemon_usable; then
		if docker_usable_for_livekit; then
			echo "Note: Docker socket not available as $(whoami); using sudo for LiveKit compose only."
			export LIVEKIT_DOCKER_SUDO=1
		else
			echo "Cannot use Docker (permission denied) and sudo docker is not available or failed." >&2
			echo "Fix Docker, add user to the docker group (then re-login), or run: SKIP_LIVEKIT_DOCKER=1 $0" >&2
			exit 1
		fi
	fi
	livekit_compose_up "${ROOT}/docker-compose.livekit.yml"
fi
sleep 2

start_ollama_if_needed
ollama_pull_model_from_env

cd "${ROOT}/backend"
# shellcheck source=/dev/null
source .venv/bin/activate

nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 >>"${ROOT}/logs/api.nohup.log" 2>&1 &
echo $! >"${ROOT}/logs/api.nohup.pid"

if [[ "${SKIP_MUSETALK:-}" != "1" ]]; then
	nohup uvicorn app.musetalk.service_app:app --host 0.0.0.0 --port 8001 \
		>>"${ROOT}/logs/musetalk.nohup.log" 2>&1 &
	echo $! >"${ROOT}/logs/musetalk.nohup.pid"
	echo "Started MuseTalk :8001 pid $(cat "${ROOT}/logs/musetalk.nohup.pid")"
else
	echo "Skipped MuseTalk (:8001) because SKIP_MUSETALK=1"
fi

nohup env PYTHONPATH=. python scripts/run_voice_worker.py >>"${ROOT}/logs/voice-worker.nohup.log" 2>&1 &
echo $! >"${ROOT}/logs/voice-worker.nohup.pid"

cd "${ROOT}/frontend"
nohup npm run dev:demo >>"${ROOT}/logs/frontend.nohup.log" 2>&1 &
echo $! >"${ROOT}/logs/frontend.nohup.pid"

echo "LiveKit: docker compose / docker-compose → docker-compose.livekit.yml (7880)"
if [[ -f "${ROOT}/logs/ollama.nohup.pid" ]]; then
	echo "Ollama pid $(cat "${ROOT}/logs/ollama.nohup.pid") (${OLLAMA_LISTEN_URL})"
elif ollama_ready; then
	echo "Ollama (${OLLAMA_LISTEN_URL}) — already running (no pid file from this script)."
fi
echo "FastAPI :8000 pid $(cat "${ROOT}/logs/api.nohup.pid")"
echo "Voice worker pid $(cat "${ROOT}/logs/voice-worker.nohup.pid")"
echo "Next :3000 pid $(cat "${ROOT}/logs/frontend.nohup.pid")"
echo "Logs: ${ROOT}/logs/*.nohup.log"
echo ""
echo "Public demo URL: in another terminal (after Next is listening on 3000), run:"
echo "  ${ROOT}/scripts/run-ngrok-tunnel.sh"
echo "Then open the printed https URL with /call (see README §5). Stop that tunnel with Ctrl+C —"
echo "stopping this stack does not stop ngrok; use scripts/stop-full-stack-nohup.sh to stop services."
