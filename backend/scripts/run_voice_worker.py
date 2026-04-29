"""Run the LiveKit Agents voice pipeline (VAD, STT→LLM→TTS).

From ``backend/`` with the API virtualenv **activated**:

  source .venv/bin/activate
  PYTHONPATH=. python scripts/run_voice_worker.py

Optional — download Silero/plugin assets (first run):

  PYTHONPATH=. python scripts/run_voice_worker.py download-files

Environment: ``LIVEKIT_URL``, ``LIVEKIT_API_KEY``, ``LIVEKIT_API_SECRET`` (see ``.env.example``),
``VOICE_API_BASE`` (FastAPI, default http://127.0.0.1:8000), ``OLLAMA_*`` for the LLM plugin.
The browser room name must match the job / room the worker joins (typical dev: create room from UI token).
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.lk_agents.voice_agent import run_worker

if __name__ == "__main__":
    run_worker()
