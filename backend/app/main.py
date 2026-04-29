import asyncio
import json
import logging
import os
import queue
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.hardware.cuda_ld_path import prepend_cuda_ld_library_path
from app.log_setup import setup_repo_file_logging
from app.llm.ollama import ollama_base_url
from app.tools.executor import execute_tool

_backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(_backend_dir / ".env")
load_dotenv()  # optional overrides from cwd
prepend_cuda_ld_library_path()

logger = logging.getLogger(__name__)
_log_file_path = setup_repo_file_logging()
if _log_file_path:
    logger.info("file_logging path=%s", _log_file_path)

_APP_VERSION = "0.8.0"


def _parse_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db.database import connect, init_db
    from app.startup_warmup import warmup_models

    conn = connect()
    init_db(conn)
    app.state.db_conn = conn
    if os.getenv("WARMUP_MODELS", "1").strip().lower() not in ("0", "false", "no"):
        await asyncio.to_thread(warmup_models)
    yield
    conn.close()


app = FastAPI(
    title="Voice Healthcare Agent API",
    version=_APP_VERSION,
    description="Backend for STT, LLM, tools, TTS, and conversation (phased rollout).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/livekit/token")
def livekit_token(
    room: str = "healthcare-demo",
    identity: str = "web-user",
    name: str | None = None,
) -> Any:
    """
    Mint a short-lived JWT for the browser LiveKit client when env keys are set and ``livekit-api`` is installed.
    Falls back to HTTP 503 — REST and WebSocket agents remain the primary path.
    """
    from app.livekit_tokens import livekit_token_service_enabled, try_build_livekit_token

    if not livekit_token_service_enabled():
        return JSONResponse(
            status_code=503,
            content={"detail": "LiveKit disabled: set LIVEKIT_API_KEY and LIVEKIT_API_SECRET on the API."},
        )
    try:
        r = (room or "").strip() or "healthcare-demo"
        ident = (identity or "").strip() or "web-user"
        return try_build_livekit_token(room=r, identity=ident, name=(name or "").strip() or None)
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"detail": str(e)})


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "voice-healthcare-agent", "version": _APP_VERSION}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _db_inspect_enabled() -> bool:
    return os.getenv("ENABLE_DB_INSPECT", "0").strip().lower() in ("1", "true", "yes", "on")


@app.get("/internal/db/snapshot")
def internal_db_snapshot(
    request: Request,
    appointments_limit: int = 50,
    messages_limit: int = 50,
    session_id: str | None = None,
) -> dict[str, Any]:
    """
    Read-only JSON view of SQLite (appointments + conversation_messages).
    **Off by default** — set ``ENABLE_DB_INSPECT=1`` in ``backend/.env`` for local use only.
    Returns **404** when disabled so the route is not advertised in production.
    """
    if not _db_inspect_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    ap_lim = max(1, min(int(appointments_limit), 200))
    msg_lim = max(1, min(int(messages_limit), 200))
    conn = request.app.state.db_conn

    ap_total = int(conn.execute("SELECT COUNT(*) AS c FROM appointments").fetchone()["c"])
    msg_total = int(conn.execute("SELECT COUNT(*) AS c FROM conversation_messages").fetchone()["c"])

    ap_rows = conn.execute(
        f"SELECT * FROM appointments ORDER BY id DESC LIMIT {ap_lim}",
    ).fetchall()

    if session_id and session_id.strip():
        sid = session_id.strip()
        msg_rows = conn.execute(
            f"SELECT * FROM conversation_messages WHERE session_id = ? ORDER BY id DESC LIMIT {msg_lim}",
            (sid,),
        ).fetchall()
    else:
        msg_rows = conn.execute(
            f"SELECT * FROM conversation_messages ORDER BY id DESC LIMIT {msg_lim}",
        ).fetchall()

    return {
        "counts": {"appointments": ap_total, "conversation_messages": msg_total},
        "appointments": [dict(r) for r in ap_rows],
        "conversation_messages": [dict(r) for r in msg_rows],
    }


def _ollama_tags_get(base_url: str) -> httpx.Response:
    return httpx.get(f"{base_url}/api/tags", timeout=3.0)


@app.get("/health/llm")
def health_llm() -> Any:
    """Probe Ollama for manual diagnostics (no LLM generation)."""
    base = ollama_base_url()
    try:
        r = _ollama_tags_get(base)
        r.raise_for_status()
        return {"ollama": "ok", "base": base}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"ollama": "unavailable", "base": base, "detail": str(e)},
        )


class ToolInvokeBody(BaseModel):
    tool: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


@app.post("/tools/invoke")
def tools_invoke(body: ToolInvokeBody, request: Request) -> dict[str, Any]:
    """Development/agent hook: execute a named tool against the SQLite-backed store."""
    conn = request.app.state.db_conn
    return execute_tool(conn, body.tool, body.arguments)


class AgentTurnBody(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field(default="default", max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)


@app.post("/agent/turn")
def agent_turn(body: AgentTurnBody, request: Request) -> dict[str, Any]:
    """Planner LLM → tool execution (if any) → finalizer LLM. Requires a running Ollama server."""
    from app.agent.runner import run_turn

    try:
        return run_turn(
            request.app.state.db_conn,
            user_message=body.message.strip(),
            session_id=(body.session_id.strip() or "default"),
            persistence_session_id=(
                body.conversation_id.strip()
                if isinstance(body.conversation_id, str) and body.conversation_id.strip()
                else None
            ),
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM service error: {e}") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class AgentSummaryBody(BaseModel):
    """Summarize transcript for ``session_id`` (hydrates from SQLite when ``CONVERSATION_PERSIST`` is on)."""

    session_id: str = Field(default="default", max_length=128)
    conversation_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional stable id for transcript storage; when set, summary loads history under this key.",
    )
    phone: str | None = Field(
        default=None,
        max_length=32,
        description="Optional E.164-style phone to list DB appointments; else session_id is tried if it looks like a phone.",
    )


@app.post("/agent/summary")
def agent_summary(body: AgentSummaryBody, request: Request) -> dict[str, Any]:
    """LLM summary + appointment snapshot + server timestamp (same session memory as conversation routes)."""
    from app.agent.summary import build_agent_summary

    sid = (body.session_id.strip() or "default")
    tid = (body.conversation_id.strip() if isinstance(body.conversation_id, str) and body.conversation_id.strip() else None)
    cost = os.getenv("INCLUDE_COST_HINTS", "0").strip().lower() in ("1", "true", "yes", "on")
    try:
        return build_agent_summary(
            request.app.state.db_conn,
            session_id=sid,
            conversation_id=tid,
            phone=(body.phone.strip() if isinstance(body.phone, str) and body.phone.strip() else None),
            include_cost_hints=cost,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM service error: {e}") from e


@app.websocket("/ws/agent")
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


@app.websocket("/ws/conversation_audio")
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


class ProcessBody(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field(default="default", max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    return_speech: bool = False


@app.post("/process")
def process_endpoint(body: ProcessBody, request: Request) -> dict[str, Any]:
    """Text → agent (LLM + tools) → optional spoken response as base64 WAV."""
    from app.conversation.pipeline import process_text_message

    try:
        return process_text_message(
            request.app.state.db_conn,
            message=body.message,
            session_id=(body.session_id.strip() or "default"),
            return_speech=body.return_speech,
            conversation_id=(
                body.conversation_id.strip()
                if isinstance(body.conversation_id, str) and body.conversation_id.strip()
                else None
            ),
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM service error: {e}") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.post("/conversation")
async def conversation_endpoint(
    request: Request,
    audio: Optional[UploadFile] = File(None),
    session_id: str = Form("default"),
    conversation_id: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    return_speech: bool = Form(True),
    message: Optional[str] = Form(None),
) -> dict[str, Any]:
    """
    Multipart-only: send either field `audio` (file: STT → agent) or `message` (string: text → agent).
    When `return_speech` is true and Piper is configured, response includes `audio_wav_base64`.
    """
    from app.conversation.pipeline import process_audio_bytes, process_text_message

    sid = (session_id or "").strip() or "default"
    cid = (conversation_id or "").strip() or None
    lang = (language or "").strip() or None

    try:
        if audio is not None and (audio.filename or "").strip():
            data = await audio.read()
            if not data:
                raise HTTPException(status_code=422, detail="Empty audio upload.")
            suffix = Path(audio.filename or "clip").suffix.lower()
            if suffix not in {".wav", ".webm", ".mp3", ".ogg", ".flac", ".m4a", ".mp4", ""}:
                suffix = ".wav"
            return process_audio_bytes(
                request.app.state.db_conn,
                audio_bytes=data,
                file_suffix=suffix,
                session_id=sid,
                language=lang,
                return_speech=return_speech,
                conversation_id=cid,
            )

        msg = (message or "").strip()
        if msg:
            return process_text_message(
                request.app.state.db_conn,
                message=msg,
                session_id=sid,
                return_speech=return_speech,
                conversation_id=cid,
            )

        raise HTTPException(
            status_code=422,
            detail="Provide multipart field `audio` (file) or non-empty `message` (string).",
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM service error: {e}") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class TTSBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@app.post("/stt")
async def stt_endpoint(
    audio: UploadFile = File(...),
    language: str | None = Form(None),
) -> dict[str, Any]:
    """
    Speech-to-text via faster-whisper. Send multipart form: field `audio` (file),
    optional field `language` (ISO-639-1, e.g. en).
    """
    from app.audio.bytes_stt import transcribe_audio_bytes

    suffix = Path(audio.filename or "clip").suffix.lower()
    if suffix not in {".wav", ".webm", ".mp3", ".ogg", ".flac", ".m4a", ".mp4", ""}:
        suffix = ".wav"
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty audio upload.")
    text, detected = transcribe_audio_bytes(data, suffix=suffix, language=language)
    return {
        "text": text,
        "language": detected,
        "warning": None if text else "Transcription empty or STT failed; see server logs.",
    }


@app.post("/tts")
def tts_endpoint(body: TTSBody) -> Response:
    """Text-to-speech via Piper CLI (`PIPER_VOICE` must point to a `.onnx` model)."""
    from app.audio.tts import TTSError, is_tts_configured, synthesize_wav_bytes

    if not is_tts_configured():
        raise HTTPException(
            status_code=503,
            detail="TTS not configured. Set PIPER_VOICE to a Piper .onnx file and install the `piper` binary.",
        )
    try:
        wav = synthesize_wav_bytes(body.text)
    except TTSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(content=wav, media_type="audio/wav")
