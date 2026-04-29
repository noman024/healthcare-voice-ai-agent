#!/usr/bin/env python3
"""
LiveKit **agent worker**: joins a room as a dedicated identity, subscribes to the user's mic,
runs the same finalize-batch pipeline as ``/ws/conversation_audio``, and echoes agent JSON events on the reliable data topic.

Requirements:
  pip install -r requirements-livekit.txt   # livekit + livekit-api

Environment (typically same ``backend/.env`` as uvicorn):

  LIVEKIT_URL=ws://127.0.0.1:7880          # signaling WebSocket URL
  LIVEKIT_API_KEY / LIVEKIT_API_SECRET      # JWT signing — match ``docker-compose.livekit.yml`` dev logs
  LIVEKIT_ROOM=healthcare-demo              # MUST match browser room string
  LIVEKIT_AGENT_IDENTITY=agent-worker       # JWT ``identity`` for this process (browser uses a different id)
  LIVEKIT_AGENT_DATA_TOPIC=lk-agent-v1      # Optional; reliable data framing (frontend must match)
  DATABASE_PATH=…                           # Same SQLite as API for tools + transcripts

Manual run::

  docker compose -f docker-compose.livekit.yml up -d      # Signal server

  Terminal A: cd backend && source .venv/bin/activate \\
    && PYTHONPATH=. python scripts/livekit_agent_worker.py

  Terminal B (browser UI): Next.js ``/call`` → LiveKit section → same room name, connect worker first or after.

Control plane matches WebSocket ``/ws/conversation_audio``: JSON over data channel::

  ``{"action":"start","session_id":"…"}`` then ``{"action":"finalize"}``

Events returned are Agent JSON (same ``type`` field contract as chunked WebSockets) with optional ``audio_wav_base64`` stripped when large.
REST (:8000/process) and ``/ws/*`` transports remain usable if LiveKit is down.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Repo backend on path when run as ``python scripts/livekit_agent_worker.py`` from ``backend/``.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND / ".env")
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [livekit.worker] %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    from app.hardware.cuda_ld_path import prepend_cuda_ld_library_path

    prepend_cuda_ld_library_path()
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    from app.db.database import connect, init_db
    from app.livekit.worker import async_main

    conn = connect()
    init_db(conn)
    try:
        asyncio.run(async_main(conn))
    except KeyboardInterrupt:
        logger.info("worker_interrupt")
        return 0
    except ValueError as e:
        logger.error("%s", e)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
