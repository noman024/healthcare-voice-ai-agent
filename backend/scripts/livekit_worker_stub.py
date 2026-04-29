#!/usr/bin/env python3
"""
Minimal LiveKit **room participant** (connectivity only — no voice pipeline).

For mic → STT → agent → data-channel events see **``scripts/livekit_agent_worker.py``**.

Proves SDK connectivity when a server is running:
  docker compose -f docker-compose.livekit.yml up

  pip install -r requirements-livekit.txt livekit
  export LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_TOKEN="<from GET /livekit/token>"

Optional fallback: if ``livekit`` RTC is missing, exit 2 with a hint (REST/WebSocket agent still works).

Uses the low-level rtc module if available (package may vary by version).
"""
from __future__ import annotations

import asyncio
import os
import sys


async def _main() -> int:
    url = os.getenv("LIVEKIT_URL", "ws://127.0.0.1:7880").rstrip("/")
    token = os.getenv("LIVEKIT_TOKEN", "").strip()
    if not token:
        print("Set LIVEKIT_TOKEN (JWT from GET /livekit/token?room=demo&identity=agent)", file=sys.stderr)
        return 1

    try:
        from livekit import rtc
    except ImportError:
        print(
            "Optional dependency missing: pip install livekit livekit-api\n"
            "Voice agent REST + /ws/agent + /ws/conversation_audio remain the supported path.",
            file=sys.stderr,
        )
        return 2

    room = rtc.Room()

    @room.on("disconnected")
    def _dc(_why: str) -> None:  # type: ignore[no-untyped-def]
        print("disconnected")

    @room.on("track_subscribed")
    def _track(_track, _pub, _p) -> None:  # type: ignore[no-untyped-def]
        print("track_subscribed")

    print(f"connecting {url} …")
    await room.connect(url, token)
    print("connected; press Ctrl+C to leave")
    try:
        await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    await room.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
