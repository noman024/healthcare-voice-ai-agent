"""
Healthcare voice agent entrypoint for LiveKit Agents.

FastAPI remains the source for HTTP, `/livekit/token`, `/tools/invoke`, `/agent/summary`, and
optional non-LiveKit transports. This worker shares the same SQLite file for tool execution.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentSession, JobContext, WorkerOptions, cli, function_tool, stt
from livekit.agents.job import AutoSubscribe
from livekit.agents.llm import ChatMessage
from livekit.agents.voice import Agent, ModelSettings
from livekit.agents.voice.events import (
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
    RunContext,
)
from livekit.agents.voice.room_io.types import AudioOutputOptions, RoomOptions, TextOutputOptions
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero

from app.audio.stt import get_whisper_model
from app.hardware.cuda_ld_path import prepend_cuda_ld_library_path
from app.llm.ollama import ollama_base_url
from app.llm.prompts import FINALIZE_SYSTEM, build_plan_system
from app.session_booking_gate import register_offered_slots, register_verified_phone
from app.tools.executor import (
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_END_CONVERSATION,
    TOOL_FETCH_SLOTS,
    TOOL_IDENTIFY_USER,
    TOOL_MODIFY_APPOINTMENT,
    TOOL_RETRIEVE_APPOINTMENTS,
    execute_tool,
)

from .stt_faster_whisper import FasterWhisperBatchSTT
from .tts_fastapi import FastApiPiperTTS
from .userdata import HealthcareUserdata

logger = logging.getLogger(__name__)


class HealthcareVoiceAgent(Agent):
    """One Piper (+ MuseTalk) job per assistant reply, not per sentence.

    LiveKit's default ``Agent.tts_node`` wraps non-streaming TTS with ``tts.StreamAdapter`` and
    BlingFire sentence tokenization, issuing ``synthesize()`` for each sentence as the LLM streams.
    That chunks room audio and avatar video sentence-by-sentence. We buffer the streamed text to
    match the WebSocket path (single WAV per turn).
    """

    async def tts_node(
        self,
        text: AsyncIterable[str],
        _model_settings: ModelSettings,
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        activity = self._get_activity_or_raise()
        if activity.tts is None:
            raise RuntimeError(
                "`tts_node` called but no TTS is configured. Disable audio with "
                "`session.output.set_audio_enabled(False)` if intentional."
            )
        parts: list[str] = []
        async for chunk in text:
            parts.append(chunk)
        full = "".join(parts).strip()
        if not full:
            return
        conn_options = self.session.conn_options.tts_conn_options
        async with activity.tts.synthesize(full, conn_options=conn_options) as stream:
            async for ev in stream:
                yield ev.frame


_BACKEND = Path(__file__).resolve().parent.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

load_dotenv(_BACKEND / ".env")
load_dotenv()


def _voice_internal_secret() -> str:
    return (os.getenv("VOICE_INTERNAL_SECRET") or "").strip()


def _post_transcript_line(
    api_base: str,
    secret: str,
    conversation_id: str,
    role: str,
    text: str,
) -> None:
    try:
        r = httpx.post(
            f"{api_base.rstrip('/')}/internal/voice/worker/transcript",
            json={"conversation_id": conversation_id, "role": role, "content": text},
            headers={"X-Voice-Internal": secret},
            timeout=10.0,
        )
        if r.status_code >= 400:
            logger.warning("worker_transcript_http_%s %s", r.status_code, r.text[:200])
    except httpx.HTTPError as e:
        logger.warning("worker_transcript_post_failed %s", e)


async def _persist_transcript_line(
    api_base: str,
    secret: str,
    conversation_id: str,
    role: str,
    text: str,
) -> None:
    if not secret or not conversation_id or not text.strip():
        return
    await asyncio.to_thread(_post_transcript_line, api_base, secret, conversation_id, role, text.strip())


async def _publish_ui_payload(room: Any, dest_identity: str, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, default=str)
    try:
        lp = room.local_participant
        await lp.publish_data(
            raw,
            reliable=True,
            topic="va",
            destination_identities=[dest_identity] if dest_identity else [],
        )
    except Exception as e:
        logger.warning("worker_publish_va_failed %s", e)


_TTS_WAV_UI_CHUNK_DEFAULT = 24_000


def _tts_ui_chunk_bytes() -> int:
    raw = os.getenv("VOICE_TTS_UI_CHUNK_BYTES", "").strip()
    if raw:
        return max(1024, int(raw))
    return _TTS_WAV_UI_CHUNK_DEFAULT


def _worker_lipsync_enabled() -> bool:
    """POST Piper WAV to ``/avatar/lipsync`` from the worker as soon as TTS returns (before browser reassembles chunks)."""
    v = os.getenv("VOICE_WORKER_LIPSYNC", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _lipsync_before_room_audio() -> bool:
    """When True (default if worker lipsync on), await full MP4 publish before emitting room PCM so browser+room align like WebSocket+attachAudio."""
    if not _worker_lipsync_enabled():
        return False
    v = os.getenv("VOICE_LIPSYNC_BEFORE_ROOM_AUDIO", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _livekit_tts_segmented() -> bool:
    """
    Sentence-scale TTS lowers time-to-first-room-audio but runs Piper + MuseTalk once per chunk, which
    gaps both streamed audio and the avatar MP4. When ``VOICE_WORKER_LIPSYNC`` is on (default), we default
    to **one** Piper + MuseTalk pass per assistant utterance unless ``VOICE_TTS_SEGMENTED`` is set explicitly.
    """
    raw = (os.getenv("VOICE_TTS_SEGMENTED") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return not _worker_lipsync_enabled()


async def _publish_lipsync_mp4_from_wav(
    room: Any,
    dest_identity: str,
    wav: bytes,
    rid: str,
    api_base: str,
) -> None:
    """
    Run MuseTalk on the API (same host as ``VOICE_API_BASE``) and stream MP4 to the browser.
    Starts GPU work immediately after Piper; no browser round-trip for the lipsync request.
    """
    if not room or not dest_identity or not wav or not rid:
        return
    base = api_base.strip().rstrip("/")
    lip_url = f"{base}/avatar/lipsync"
    timeout_s = float((os.getenv("VOICE_WORKER_LIPSYNC_TIMEOUT_SEC") or "300").strip() or "300")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=True,
        ) as client:
            r = await client.post(
                lip_url,
                files={"audio": ("tts.wav", wav, "audio/wav")},
            )
    except Exception as e:
        logger.warning("worker_lipsync_post_failed rid=%s error=%s", rid, e)
        await _publish_ui_payload(
            room,
            dest_identity,
            {"kind": "lipsync_mp4_error", "rid": rid, "message": str(e)[:200]},
        )
        return
    if r.status_code >= 400:
        logger.warning(
            "worker_lipsync_http rid=%s status=%s body=%s",
            rid,
            r.status_code,
            (r.text or "")[:200],
        )
        await _publish_ui_payload(
            room,
            dest_identity,
            {"kind": "lipsync_mp4_error", "rid": rid, "http": r.status_code},
        )
        return
    mp4 = r.content
    if not mp4:
        await _publish_ui_payload(room, dest_identity, {"kind": "lipsync_mp4_error", "rid": rid})
        return
    chunk_sz = _tts_ui_chunk_bytes()
    nchunks = (len(mp4) + chunk_sz - 1) // chunk_sz
    seq = 0
    for i in range(0, len(mp4), chunk_sz):
        piece = mp4[i : i + chunk_sz]
        payload: dict[str, Any] = {
            "kind": "lipsync_mp4_chunk",
            "rid": rid,
            "seq": seq,
            "last": seq == nchunks - 1,
            "b64": base64.b64encode(piece).decode("ascii"),
        }
        await _publish_ui_payload(room, dest_identity, payload)
        seq += 1


def _load_livekit_vad() -> silero.VAD:
    def _f(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        return default if not raw else float(raw)

    # Faster endpointing than Silero defaults (0.55s silence / 0.5s prefix) when env omitted.
    return silero.VAD.load(
        min_speech_duration=_f("VOICE_LIVEKIT_VAD_MIN_SPEECH", 0.05),
        min_silence_duration=_f("VOICE_LIVEKIT_VAD_MIN_SILENCE", 0.35),
        prefix_padding_duration=_f("VOICE_LIVEKIT_VAD_PREFIX_PADDING", 0.22),
        activation_threshold=_f("VOICE_LIVEKIT_VAD_ACTIVATION_THRESHOLD", 0.5),
    )


async def _publish_tts_wav_body(room: Any, dest_identity: str, wav: bytes, rid: str) -> None:
    """Chunked WAV payload after ``tts_begin`` (runs in background so room audio is not blocked)."""
    if not room or not dest_identity or not wav or not rid:
        return
    chunk_sz = _tts_ui_chunk_bytes()
    nchunks = (len(wav) + chunk_sz - 1) // chunk_sz
    seq = 0
    for i in range(0, len(wav), chunk_sz):
        piece = wav[i : i + chunk_sz]
        payload = {
            "kind": "tts_wav_chunk",
            "rid": rid,
            "seq": seq,
            "last": seq == nchunks - 1,
            "b64": base64.b64encode(piece).decode("ascii"),
        }
        await _publish_ui_payload(room, dest_identity, payload)
        seq += 1


async def _publish_tts_wav_chunks(
    room: Any,
    dest_identity: str,
    wav: bytes,
    api_base: str,
) -> None:
    """Send ``tts_begin`` (awaited), WAV chunks, and optionally worker-driven MuseTalk MP4 chunks."""
    if not room or not dest_identity or not wav:
        return
    rid = uuid.uuid4().hex[:16]
    worker_lip = _worker_lipsync_enabled()
    begin: dict[str, Any] = {"kind": "tts_begin", "rid": rid}
    if worker_lip:
        begin["worker_lipsync"] = True
    await _publish_ui_payload(room, dest_identity, begin)
    gate = _lipsync_before_room_audio()
    if worker_lip and gate:
        await _publish_lipsync_mp4_from_wav(room, dest_identity, wav, rid, api_base)
    if gate:
        await _publish_tts_wav_body(room, dest_identity, wav, rid)
    else:
        asyncio.create_task(_publish_tts_wav_body(room, dest_identity, wav, rid))
    if worker_lip and not gate:
        asyncio.create_task(_publish_lipsync_mp4_from_wav(room, dest_identity, wav, rid, api_base))


async def _publish_tts_segment_chunks(
    room: Any,
    dest_identity: str,
    wav: bytes,
    api_base: str,
    *,
    utterance_id: str,
    segment_index: int,
    segment_count: int,
    audio_offset_ms: float,
) -> None:
    """
    One Piper+MuseTalk segment: UI aligns MP4 to room audio via ``audio_offset_ms`` from utterance start.
    """
    if not room or not dest_identity or not wav:
        return
    rid = f"{utterance_id}_{segment_index}"[:128]
    worker_lip = _worker_lipsync_enabled()
    begin: dict[str, Any] = {
        "kind": "tts_begin",
        "utterance_id": utterance_id,
        "segment_index": segment_index,
        "segment_count": segment_count,
        "audio_offset_ms": round(float(audio_offset_ms), 2),
        "rid": rid,
    }
    if worker_lip:
        begin["worker_lipsync"] = True
    await _publish_ui_payload(room, dest_identity, begin)
    gate = _lipsync_before_room_audio()
    if worker_lip and gate:
        await _publish_lipsync_mp4_from_wav(room, dest_identity, wav, rid, api_base)
    if gate:
        await _publish_tts_wav_body(room, dest_identity, wav, rid)
    else:
        asyncio.create_task(_publish_tts_wav_body(room, dest_identity, wav, rid))
    if worker_lip and not gate:
        asyncio.create_task(_publish_lipsync_mp4_from_wav(room, dest_identity, wav, rid, api_base))


def _tool_result_payload(out: dict[str, Any]) -> str:
    return json.dumps(out, default=str)


async def _voice_execute_tool(
    u: HealthcareUserdata,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    session_id: str | None,
) -> dict[str, Any]:
    """Publish in-flight tool status to the browser, then run SQLite tools off the event loop."""
    if u.room and u.ui_dest_identity:
        await _publish_ui_payload(
            u.room,
            u.ui_dest_identity,
            {"kind": "tool", "tool_execution": {"phase": "running", "tool": tool_name}},
        )
    return await asyncio.to_thread(
        execute_tool,
        u.conn,
        tool_name,
        arguments,
        session_id=session_id,
    )


def _voice_system_instructions() -> str:
    from datetime import date

    planner = build_plan_system(today_iso=date.today().isoformat())
    return (
        planner
        + "\n\n---\nYou are speaking with a patient over voice. "
        "Call tools to act; keep spoken replies short and natural. "
        "After a tool result, summarize in plain language (no internal tool names).\n"
    )


@function_tool
async def lk_identify_user(ctx: RunContext[HealthcareUserdata], phone: str, name: str | None = None) -> str:
    u = ctx.userdata
    prior_sid = u.session_key
    out = await _voice_execute_tool(
        u,
        TOOL_IDENTIFY_USER,
        {"phone": phone, **({"name": name} if name else {})},
        session_id=None,
    )
    if out.get("success"):
        data = out.get("data") or {}
        ph = data.get("phone")
        if isinstance(ph, str) and ph.strip():
            ph = ph.strip()
            register_verified_phone(prior_sid, ph)
            register_verified_phone(ph, ph)
            u.session_key = ph
    return _tool_result_payload(out)


@function_tool
async def lk_fetch_slots(ctx: RunContext[HealthcareUserdata], date: str) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(u, TOOL_FETCH_SLOTS, {"date": date}, session_id=u.session_key)
    if out.get("success") and isinstance(out.get("data"), dict):
        d = out["data"]
        register_offered_slots(u.session_key, str(d.get("date") or ""), d.get("available_slots") or [])
    return _tool_result_payload(out)


@function_tool
async def lk_book_appointment(
    ctx: RunContext[HealthcareUserdata],
    name: str,
    phone: str,
    date: str,
    time: str,
) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(
        u,
        TOOL_BOOK_APPOINTMENT,
        {"name": name, "phone": phone, "date": date, "time": time},
        session_id=u.session_key,
    )
    return _tool_result_payload(out)


@function_tool
async def lk_retrieve_appointments(
    ctx: RunContext[HealthcareUserdata],
    phone: str,
    include_cancelled: bool = False,
) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(
        u,
        TOOL_RETRIEVE_APPOINTMENTS,
        {"phone": phone, "include_cancelled": include_cancelled},
        session_id=u.session_key,
    )
    return _tool_result_payload(out)


@function_tool
async def lk_cancel_appointment(
    ctx: RunContext[HealthcareUserdata],
    appointment_id: int,
    phone: str,
) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(
        u,
        TOOL_CANCEL_APPOINTMENT,
        {"appointment_id": appointment_id, "phone": phone},
        session_id=u.session_key,
    )
    return _tool_result_payload(out)


@function_tool
async def lk_modify_appointment(
    ctx: RunContext[HealthcareUserdata],
    appointment_id: int,
    phone: str,
    new_date: str,
    new_time: str,
) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(
        u,
        TOOL_MODIFY_APPOINTMENT,
        {
            "appointment_id": appointment_id,
            "phone": phone,
            "new_date": new_date,
            "new_time": new_time,
        },
        session_id=u.session_key,
    )
    return _tool_result_payload(out)


@function_tool
async def lk_end_conversation(ctx: RunContext[HealthcareUserdata], reason: str | None = None) -> str:
    u = ctx.userdata
    args: dict[str, Any] = {}
    if reason:
        args["reason"] = reason
    out = await _voice_execute_tool(u, TOOL_END_CONVERSATION, args, session_id=u.session_key)
    return _tool_result_payload(out)


_HEALTH_TOOLS = [
    lk_identify_user,
    lk_fetch_slots,
    lk_book_appointment,
    lk_retrieve_appointments,
    lk_cancel_appointment,
    lk_modify_appointment,
    lk_end_conversation,
]


async def entrypoint(ctx: JobContext) -> None:
    prepend_cuda_ld_library_path()

    from app.db.database import connect, init_db

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    try:
        participant = await ctx.wait_for_participant()
    except RuntimeError as exc:
        # Common when the browser reconnects / Strict Mode unmount disconnects early.
        if "disconnect" in str(exc).lower():
            logger.info("LiveKit job exit: %s", exc)
            return
        raise
    identity = str(getattr(participant, "identity", None) or "voice-user")

    raw_meta = str(getattr(participant, "metadata", None) or "").strip()
    conversation_id: str | None = None
    if raw_meta:
        try:
            meta_obj = json.loads(raw_meta)
            if isinstance(meta_obj, dict):
                cid_raw = meta_obj.get("conversation_id")
                if isinstance(cid_raw, str) and cid_raw.strip():
                    conversation_id = cid_raw.strip()
        except json.JSONDecodeError:
            logger.debug("livekit_participant_metadata_not_json")

    conn = connect()
    init_db(conn)

    await asyncio.to_thread(get_whisper_model)

    api_base = os.getenv("VOICE_API_BASE", "http://127.0.0.1:8000").strip().rstrip("/")
    publish_sr_raw = os.getenv("VOICE_PUBLISH_SAMPLE_RATE", "").strip()
    publish_sr = (
        int(publish_sr_raw)
        if publish_sr_raw
        else 24_000  # Same default as LiveKit agents RoomIO AudioOutputOptions
    )
    ollama_url = f"{ollama_base_url().rstrip('/')}/v1"
    model = (os.getenv("OLLAMA_MODEL") or "qwen2.5:7b-instruct").strip()

    userdata = HealthcareUserdata(
        conn=conn,
        session_key=identity,
        conversation_id=conversation_id,
        room=ctx.room,
        ui_dest_identity=identity,
    )
    vad = _load_livekit_vad()
    batch_stt = FasterWhisperBatchSTT()
    pipeline_stt = stt.StreamAdapter(stt=batch_stt, vad=vad)

    llm_engine = lk_openai.LLM(
        model=model,
        base_url=ollama_url,
        api_key="ollama",
    )

    async def _forward_wav_to_ui(wav: bytes) -> None:
        await _publish_tts_wav_chunks(ctx.room, identity, wav, api_base)

    async def _forward_segment_to_ui(
        wav: bytes,
        segment_index: int,
        segment_count: int,
        audio_offset_ms: float,
        utterance_id: str,
    ) -> None:
        await _publish_tts_segment_chunks(
            ctx.room,
            identity,
            wav,
            api_base,
            utterance_id=utterance_id,
            segment_index=segment_index,
            segment_count=segment_count,
            audio_offset_ms=audio_offset_ms,
        )

    seg_effective = _livekit_tts_segmented()
    tts_engine = FastApiPiperTTS(
        base_url=api_base,
        publish_sample_rate=publish_sr,
        on_original_wav=_forward_wav_to_ui if not seg_effective else None,
        on_segment_wav=_forward_segment_to_ui if seg_effective else None,
        segmented=seg_effective,
    )

    instructions = _voice_system_instructions() + "\n---\nResponse tone:\n" + FINALIZE_SYSTEM
    agent = HealthcareVoiceAgent(instructions=instructions, tools=_HEALTH_TOOLS)

    session = AgentSession(
        stt=pipeline_stt,
        llm=llm_engine,
        tts=tts_engine,
        vad=vad,
        userdata=userdata,
    )

    internal_secret = _voice_internal_secret()
    browser_identity = identity

    def on_conversation_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, ChatMessage):
            return
        if item.role not in ("user", "assistant"):
            return
        text = item.text_content
        if not text or not text.strip():
            return
        cid = userdata.conversation_id
        if not cid or not internal_secret:
            if not internal_secret:
                logger.warning("worker_transcript_skipped VOICE_INTERNAL_SECRET unset — Summary API will rely on transcript_fallback from browser")
            elif not cid:
                logger.warning("worker_transcript_skipped no conversation_id in participant metadata")
            return
        asyncio.create_task(
            _persist_transcript_line(
                api_base,
                internal_secret,
                cid,
                str(item.role),
                text,
            ),
        )

    def on_function_tools(ev: FunctionToolsExecutedEvent) -> None:
        for call, out in ev.zipped():
            if out and out.output:
                try:
                    parsed: dict[str, Any] = json.loads(out.output)
                except json.JSONDecodeError:
                    parsed = {
                        "success": False,
                        "tool": call.name,
                        "error": {"message": (out.output or "")[:400]},
                    }
            else:
                parsed = {"success": False, "tool": call.name, "error": {"message": "no output"}}
            if not isinstance(parsed, dict):
                parsed = {"success": False, "tool": call.name}
            asyncio.create_task(
                _publish_ui_payload(ctx.room, browser_identity, {"kind": "tool", "tool_execution": parsed}),
            )
            tool_name = str(parsed.get("tool") or "")
            if parsed.get("success") is True and tool_name == TOOL_END_CONVERSATION:
                asyncio.create_task(_publish_ui_payload(ctx.room, browser_identity, {"kind": "conversation_ended"}))

    session.on("conversation_item_added", on_conversation_item)
    session.on("function_tools_executed", on_function_tools)

    sync_captions_to_audio = os.getenv(
        "VOICE_SYNC_TRANSCRIPTION_TO_AUDIO",
        "",
    ).strip().lower() in ("1", "true", "yes")
    text_output_opts = (
        TextOutputOptions()
        if sync_captions_to_audio
        else TextOutputOptions(sync_transcription=False)
    )

    room_options = RoomOptions(
        audio_output=AudioOutputOptions(sample_rate=publish_sr),
        text_output=text_output_opts,
    )

    try:
        await session.start(agent=agent, room=ctx.room, room_options=room_options)
    finally:
        conn.close()


def run_worker() -> None:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        ),
    )


if __name__ == "__main__":
    run_worker()
