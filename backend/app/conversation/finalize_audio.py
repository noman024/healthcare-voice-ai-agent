"""Shared finalize-batch audio → STT → agent pipeline (WebSocket and batch callers).

All transports ingest one bounded byte buffer (browser WebM/WAV mic capture)
and reuse the same event stream as :func:`~app.conversation.pipeline.iter_chunked_audio_turn_events`.
"""

from __future__ import annotations

from typing import Any, Iterator

import sqlite3

from app.conversation.pipeline import iter_chunked_audio_turn_events


def iter_finalize_batch_turn_events(
    conn: sqlite3.Connection,
    *,
    audio_bytes: bytes,
    file_suffix: str,
    session_id: str,
    language: str | None,
    return_speech: bool,
    conversation_id: str | None = None,
    client: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Yield the same dictionaries as chunked WebSocket ``/ws/conversation_audio``.
    Thin wrapper so REST batch callers stay aligned with WebSocket semantics.
    """
    yield from iter_chunked_audio_turn_events(
        conn,
        audio_bytes=audio_bytes,
        file_suffix=file_suffix,
        session_id=session_id,
        language=language,
        return_speech=return_speech,
        conversation_id=conversation_id,
        client=client,
    )


def strip_agent_event_for_data_transport(ev: dict[str, Any]) -> dict[str, Any]:
    """
    Agent events may contain large WAV base64 payloads; unreliable/small-data channels omit them.

    Consumers can still synthesize speech via REST ``POST /tts`` using ``final_response``.
    """
    out = {k: v for k, v in ev.items() if k != "audio_wav_base64"}
    return out
