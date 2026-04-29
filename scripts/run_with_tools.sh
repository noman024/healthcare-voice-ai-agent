#!/usr/bin/env bash
# Prepend repo `.tools/ollama` to PATH and LD_LIBRARY_PATH, then exec a command.
# Example: ./scripts/run_with_tools.sh ollama serve
# Example: cd backend && ../scripts/run_with_tools.sh .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$REPO_ROOT/.tools/ollama/bin${PATH:+:$PATH}"
OLLAMA_LIB="$REPO_ROOT/.tools/ollama/lib/ollama"
export LD_LIBRARY_PATH="$OLLAMA_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$@"
