"""
Healthcare voice agent entrypoint for LiveKit Agents.

FastAPI remains the source for HTTP, `/livekit/token`, `/tools/invoke`, `/agent/summary`, and
optional non-LiveKit transports. This worker shares the same SQLite file for tool execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncGenerator, AsyncIterable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentSession, JobContext, WorkerOptions, cli, stt
from livekit.agents.job import AutoSubscribe
from livekit.agents.llm import ChatMessage
from livekit.agents.voice import Agent, ModelSettings
from livekit.agents.voice.events import (
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
)
from livekit.agents.voice.room_io.types import AudioOutputOptions, RoomOptions, TextOutputOptions
from livekit.plugins import openai as lk_openai

from app.audio.stt import get_whisper_model
from app.hardware.cuda_ld_path import prepend_cuda_ld_library_path
from app.llm.ollama import ollama_base_url
from app.llm.prompts import FINALIZE_SYSTEM
from app.tools.executor import TOOL_END_CONVERSATION

from .stt_faster_whisper import FasterWhisperBatchSTT
from .tts_fastapi import FastApiPiperTTS
from .userdata import HealthcareUserdata
from .voice_function_tools import HEALTH_TOOLS, voice_system_instructions
from .worker_env import livekit_tts_segmented as _livekit_tts_segmented
from .worker_publish import publish_tts_segment_chunks, publish_tts_wav_chunks, publish_ui_payload
from .worker_transcript import persist_transcript_line, voice_internal_secret
from .worker_vad import load_livekit_vad

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
    vad = load_livekit_vad()
    batch_stt = FasterWhisperBatchSTT()
    pipeline_stt = stt.StreamAdapter(stt=batch_stt, vad=vad)

    llm_engine = lk_openai.LLM(
        model=model,
        base_url=ollama_url,
        api_key="ollama",
    )

    async def _forward_wav_to_ui(wav: bytes) -> None:
        await publish_tts_wav_chunks(ctx.room, identity, wav, api_base)

    async def _forward_segment_to_ui(
        wav: bytes,
        segment_index: int,
        segment_count: int,
        audio_offset_ms: float,
        utterance_id: str,
    ) -> None:
        await publish_tts_segment_chunks(
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

    instructions = voice_system_instructions() + "\n---\nResponse tone:\n" + FINALIZE_SYSTEM
    agent = HealthcareVoiceAgent(instructions=instructions, tools=HEALTH_TOOLS)

    session = AgentSession(
        stt=pipeline_stt,
        llm=llm_engine,
        tts=tts_engine,
        vad=vad,
        userdata=userdata,
    )

    internal_secret = voice_internal_secret()
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
            persist_transcript_line(
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
                publish_ui_payload(ctx.room, browser_identity, {"kind": "tool", "tool_execution": parsed}),
            )
            tool_name = str(parsed.get("tool") or "")
            if parsed.get("success") is True and tool_name == TOOL_END_CONVERSATION:
                asyncio.create_task(publish_ui_payload(ctx.room, browser_identity, {"kind": "conversation_ended"}))

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
