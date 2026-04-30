"""Helpers for streaming blocking iterators over WebSocket JSON frames."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Iterator
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


async def send_blocking_iterator_over_websocket(
    websocket: WebSocket,
    events: Iterator[dict[str, Any]],
    *,
    queue_maxsize: int,
    log_event: str,
) -> None:
    """
    Run ``events`` on a daemon thread and forward each dict with ``send_json``.

    On exception, logs ``log_event``, sends ``{"type":"error","message":...}``, then finishes the
    stream (same behavior as the previous inline WebSocket handlers). The producer always pushes
    a final ``None`` sentinel so the async side stops.
    """
    q: queue.Queue = queue.Queue(maxsize=queue_maxsize)

    def producer() -> None:
        try:
            for ev in events:
                q.put(ev)
        except Exception as e:
            logger.exception(log_event)
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=producer, daemon=True).start()
    while True:
        ev = await asyncio.to_thread(q.get)
        if ev is None:
            break
        await websocket.send_json(ev)
