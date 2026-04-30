from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.routers.ws_streaming import send_blocking_iterator_over_websocket

router = APIRouter(tags=["websockets"])


@router.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket) -> None:
    """
    JSON-over-WebSocket agent turns (REST remains the default transport).

    Client → server (text): ``{\"action\":\"turn\",\"message\":\"...\",\"session_id\":\"...\"}`` or ``{\"action\":\"ping\"}``.
    Server → client: ``plan``, ``tool`` (may arrive twice when ``phase: running`` precedes execution), ``done`` — same shapes as ``iter_turn_events`` — or ``error``.
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

            await send_blocking_iterator_over_websocket(
                websocket,
                iter_turn_events(
                    conn,
                    user_message=msg,
                    session_id=sid,
                    persistence_session_id=cid,
                ),
                queue_maxsize=64,
                log_event="ws_agent_turn_failed",
            )
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

                    await send_blocking_iterator_over_websocket(
                        websocket,
                        iter_finalize_batch_turn_events(
                            conn,
                            audio_bytes=audio,
                            file_suffix=str(meta.get("file_extension") or ".webm"),
                            session_id=str(meta.get("session_id") or "default"),
                            language=meta.get("language"),
                            return_speech=bool(meta.get("return_speech", True)),
                            conversation_id=meta.get("conversation_id"),
                        ),
                        queue_maxsize=128,
                        log_event="ws_conversation_audio_failed",
                    )
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
