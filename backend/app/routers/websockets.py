from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websockets"])
logger = logging.getLogger(__name__)


@router.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket) -> None:
    """
    JSON-over-WebSocket agent turns (REST remains the default transport).

    Client → server (text): ``{\"action\":\"turn\",\"message\":\"...\",\"session_id\":\"...\"}`` or ``{\"action\":\"ping\"}``.
    Server → client: ``plan`` / ``tool`` / ``done`` events (same shapes as ``iter_turn_events``), or ``error``.
    """
    await websocket.accept()
    conn = websocket.app.state.db_conn
    from app.agent.runner import iter_turn_events

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue
            action = str(payload.get("action") or "").strip().lower()
            if action == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if action != "turn":
                await websocket.send_json(
                    {"type": "error", "message": "Unknown action; use turn or ping."},
                )
                continue
            msg = str(payload.get("message") or "").strip()
            if not msg:
                await websocket.send_json({"type": "error", "message": "message required"})
                continue
            sid = str(payload.get("session_id") or "default").strip() or "default"
            cid_raw = payload.get("conversation_id")
            cid = str(cid_raw).strip() if cid_raw not in (None, "") else None

            q: queue.Queue = queue.Queue(maxsize=64)

            def producer() -> None:
                try:
                    for ev in iter_turn_events(
                        conn,
                        user_message=msg,
                        session_id=sid,
                        persistence_session_id=cid,
                    ):
                        q.put(ev)
                except Exception as e:
                    logger.exception("ws_agent_turn_failed")
                    q.put({"type": "error", "message": str(e)})
                finally:
                    q.put(None)

            threading.Thread(target=producer, daemon=True).start()
            while True:
                ev = await asyncio.to_thread(q.get)
                if ev is None:
                    break
                await websocket.send_json(ev)
    except WebSocketDisconnect:
        return


@router.websocket("/ws/conversation_audio")
async def ws_conversation_audio(websocket: WebSocket) -> None:
    """
    Chunked binary audio → STT → same ``plan`` / ``tool`` / ``done`` stream as ``/ws/agent``.

    Control (text JSON): ``{\"action\":\"start\", \"session_id\", \"language\", \"return_speech\", \"file_extension\"}``,
    then send one or more **binary** frames (webm/wav bytes), then ``{\"action\":\"finalize\"}``.
    Optional ``{\"action\":\"ping\"}`` → ``{\"type\":\"pong\"}``.
    First server message after ``start``: ``{\"type\":\"ready\", ...}``. After ``finalize``:
    ``stt_started`` → ``stt`` (transcript) → same ``plan`` / ``tool`` / ``done`` as ``/ws/agent``.
    """
    await websocket.accept()
    conn = websocket.app.state.db_conn
    from app.conversation.finalize_audio import iter_finalize_batch_turn_events

    buf = bytearray()
    meta: dict[str, Any] = {}

    try:
        while True:
            raw_msg = await websocket.receive()
            if raw_msg.get("type") == "websocket.disconnect":
                return

            if "text" in raw_msg and raw_msg["text"] is not None:
                try:
                    payload = json.loads(str(raw_msg["text"]))
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                    continue
                action = str(payload.get("action") or "").strip().lower()
                if action == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                if action == "start":
                    buf.clear()
                    sid = str(payload.get("session_id") or "default").strip() or "default"
                    lang_raw = payload.get("language")
                    lang = str(lang_raw).strip() if lang_raw not in (None, "") else None
                    cid_raw = payload.get("conversation_id")
                    cid = str(cid_raw).strip() if cid_raw not in (None, "") else None
                    meta = {
                        "session_id": sid,
                        "conversation_id": cid,
                        "language": lang,
                        "return_speech": bool(payload.get("return_speech", True)),
                        "file_extension": str(payload.get("file_extension") or ".webm"),
                    }
                    await websocket.send_json({"type": "ready", "session_id": sid})
                    continue
                if action == "finalize":
                    if not meta:
                        await websocket.send_json(
                            {"type": "error", "message": "Send action=start before finalize."},
                        )
                        continue
                    audio = bytes(buf)

                    aq: queue.Queue = queue.Queue(maxsize=128)

                    def producer() -> None:
                        try:
                            for ev in iter_finalize_batch_turn_events(
                                conn,
                                audio_bytes=audio,
                                file_suffix=str(meta.get("file_extension") or ".webm"),
                                session_id=str(meta.get("session_id") or "default"),
                                language=meta.get("language"),
                                return_speech=bool(meta.get("return_speech", True)),
                                conversation_id=meta.get("conversation_id"),
                            ):
                                aq.put(ev)
                        except Exception as e:
                            logger.exception("ws_conversation_audio_failed")
                            aq.put({"type": "error", "message": str(e)})
                        finally:
                            aq.put(None)

                    threading.Thread(target=producer, daemon=True).start()
                    while True:
                        ev = await asyncio.to_thread(aq.get)
                        if ev is None:
                            break
                        await websocket.send_json(ev)
                    continue

                await websocket.send_json(
                    {"type": "error", "message": "Unknown action; use start, finalize, or ping."},
                )
                continue

            if "bytes" in raw_msg and raw_msg["bytes"] is not None:
                buf.extend(raw_msg["bytes"])
                continue
    except WebSocketDisconnect:
        return
