"""Full turn: optional STT → agent → optional TTS (speech or text in)."""

from __future__ import annotations

import base64
import logging
import os
import time
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from app.agent.runner import iter_turn_events, run_turn
from app.audio.bytes_stt import normalize_suffix, transcribe_audio_bytes
from app.audio.tts import TTSError, is_tts_configured, synthesize_wav_bytes

logger = logging.getLogger(__name__)


def maybe_tts_base64(text: str, *, want_audio: bool) -> tuple[str | None, bool]:
    """
    If want_audio and Piper is configured, return (base64_wav, True).
    Otherwise (None, tts_was_configured).
    """
    if not want_audio:
        return None, is_tts_configured()
    if not is_tts_configured():
        return None, False
    try:
        wav = synthesize_wav_bytes(text)
        return base64.b64encode(wav).decode("ascii"), True
    except TTSError as e:
        logger.warning("tts_failed in pipeline: %s", e)
        return None, True


def process_text_message(
    conn: sqlite3.Connection,
    *,
    message: str,
    session_id: str,
    return_speech: bool,
    conversation_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Text in → agent turn → optional Speech-Out as base64 WAV."""
    msg = message.strip()
    agent = run_turn(
        conn,
        user_message=msg,
        session_id=session_id,
        persistence_session_id=conversation_id,
        client=client,
    )
    audio_b64, _tts_ok = maybe_tts_base64(agent["final_response"], want_audio=return_speech)
    return {
        "mode": "text",
        "session_id": session_id,
        "transcript": msg,
        "detected_language": None,
        **agent,
        "audio_wav_base64": audio_b64,
        "tts_configured": is_tts_configured(),
    }


def process_audio_bytes(
    conn: sqlite3.Connection,
    *,
    audio_bytes: bytes,
    file_suffix: str,
    session_id: str,
    language: str | None,
    return_speech: bool,
    conversation_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """In-memory audio → STT → agent → optional Speech-Out (same JSON as multipart /conversation)."""
    suf = normalize_suffix(file_suffix)
    t0 = time.perf_counter()
    text, detected = transcribe_audio_bytes(audio_bytes, suffix=suf, language=language)
    stt_elapsed_ms = int((time.perf_counter() - t0) * 1000.0)
    audio_byte_len = len(audio_bytes)
    text = (text or "").strip()
    if not text:
        audio_b64, _ = maybe_tts_base64(
            "I didn't catch that—could you say it again?",
            want_audio=return_speech,
        )
        return {
            "mode": "audio",
            "session_id": session_id,
            "transcript": "",
            "detected_language": detected,
            "final_response": "",
            "plan": None,
            "tool_execution": None,
            "audio_wav_base64": audio_b64,
            "tts_configured": is_tts_configured(),
            "warning": "Empty transcript; STT produced no text.",
            "stt_elapsed_ms": stt_elapsed_ms,
            "audio_byte_len": audio_byte_len,
        }

    agent = run_turn(
        conn,
        user_message=text,
        session_id=session_id,
        persistence_session_id=conversation_id,
        client=client,
    )
    audio_b64, _ = maybe_tts_base64(agent["final_response"], want_audio=return_speech)
    return {
        "mode": "audio",
        "session_id": session_id,
        "transcript": text,
        "detected_language": detected,
        **agent,
        "audio_wav_base64": audio_b64,
        "tts_configured": is_tts_configured(),
        "stt_elapsed_ms": stt_elapsed_ms,
        "audio_byte_len": audio_byte_len,
    }


def process_audio_message(
    conn: sqlite3.Connection,
    *,
    audio_path: str | Path,
    session_id: str,
    language: str | None,
    return_speech: bool,
    conversation_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Audio file → STT → agent → optional Speech-Out as base64 WAV."""
    path = Path(audio_path)
    data = path.read_bytes()
    return process_audio_bytes(
        conn,
        audio_bytes=data,
        file_suffix=path.suffix or ".wav",
        session_id=session_id,
        language=language,
        return_speech=return_speech,
        conversation_id=conversation_id,
        client=client,
    )


def iter_chunked_audio_turn_events(
    conn: sqlite3.Connection,
    *,
    audio_bytes: bytes,
    file_suffix: str,
    session_id: str,
    language: str | None,
    return_speech: bool,
    conversation_id: str | None = None,
    client: httpx.Client | None = None,
) -> Iterator[dict[str, Any]]:
    """
    WebSocket-friendly: emit ``stt_started`` (before Whisper runs), then ``stt`` with the transcript, then the
    same ``plan`` / ``tool`` / ``done`` stream as ``iter_turn_events``, with ``done`` augmented by
    ``transcript``, ``detected_language``, optional ``audio_wav_base64``, and ``mode``.
    """
    suf = normalize_suffix(file_suffix)
    yield {"type": "stt_started", "session_id": session_id}
    t0 = time.perf_counter()
    text, detected = transcribe_audio_bytes(audio_bytes, suffix=suf, language=language)
    stt_elapsed_ms = int((time.perf_counter() - t0) * 1000.0)
    audio_byte_len = len(audio_bytes)
    text = (text or "").strip()
    yield {
        "type": "stt",
        "session_id": session_id,
        "transcript": text,
        "detected_language": detected,
        "stt_elapsed_ms": stt_elapsed_ms,
        "audio_byte_len": audio_byte_len,
    }
    if not text:
        audio_b64, _ = maybe_tts_base64(
            "I didn't catch that—could you say it again?",
            want_audio=return_speech,
        )
        yield {
            "type": "done",
            "session_id": session_id,
            "final_response": "",
            "plan": None,
            "tool_execution": None,
            "audio_wav_base64": audio_b64,
            "tts_configured": is_tts_configured(),
            "warning": "Empty transcript; STT produced no text.",
            "transcript": "",
            "detected_language": detected,
            "mode": "audio",
            "session_identity": None,
            "stt_elapsed_ms": stt_elapsed_ms,
            "audio_byte_len": audio_byte_len,
        }
        return

    t_agent0 = time.perf_counter()
    _ws_timing = os.getenv("VOICE_WS_PIPELINE_TIMING", "").strip().lower() in ("1", "true", "yes", "on")

    for ev in iter_turn_events(
        conn,
        user_message=text,
        session_id=session_id,
        persistence_session_id=conversation_id,
        client=client,
    ):
        if ev.get("type") != "done":
            yield ev
            continue
        payload = {k: v for k, v in ev.items() if k != "type"}
        t_before_tts = time.perf_counter()
        audio_b64, _ = maybe_tts_base64(str(payload.get("final_response") or ""), want_audio=return_speech)
        if _ws_timing:
            logger.info(
                "ws_voice_timing session=%s stt_ms=%s agent_llm_tool_ms=%s piper_tts_ms=%s",
                session_id,
                stt_elapsed_ms,
                int((t_before_tts - t_agent0) * 1000.0),
                int((time.perf_counter() - t_before_tts) * 1000.0),
            )
        payload["audio_wav_base64"] = audio_b64
        payload["tts_configured"] = is_tts_configured()
        payload["transcript"] = text
        payload["detected_language"] = detected
        payload["mode"] = "audio"
        payload["stt_elapsed_ms"] = stt_elapsed_ms
        payload["audio_byte_len"] = audio_byte_len
        yield {"type": "done", **payload}
