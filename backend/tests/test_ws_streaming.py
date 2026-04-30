"""Tests for WebSocket blocking-iterator bridge."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.routers.ws_streaming import send_blocking_iterator_over_websocket


def test_send_blocking_iterator_streams_and_stops() -> None:
    sent: list[dict] = []

    async def _run() -> None:
        ws = AsyncMock()
        ws.send_json = AsyncMock(side_effect=lambda m: sent.append(m))

        def gen():
            yield {"type": "a", "n": 1}
            yield {"type": "b", "n": 2}

        await send_blocking_iterator_over_websocket(
            ws, gen(), queue_maxsize=8, log_event="test_ws_stream_ok"
        )

    asyncio.run(_run())
    assert sent == [{"type": "a", "n": 1}, {"type": "b", "n": 2}]


def test_send_blocking_iterator_on_exception_sends_error() -> None:
    sent: list[dict] = []

    async def _run() -> None:
        ws = AsyncMock()
        ws.send_json = AsyncMock(side_effect=lambda m: sent.append(m))

        def gen():
            yield {"type": "ok"}
            msg = "boom"
            raise RuntimeError(msg)

        await send_blocking_iterator_over_websocket(
            ws, gen(), queue_maxsize=8, log_event="test_ws_stream_err"
        )

    asyncio.run(_run())
    assert len(sent) == 2
    assert sent[0] == {"type": "ok"}
    assert sent[1]["type"] == "error"
    assert "boom" in sent[1]["message"]
