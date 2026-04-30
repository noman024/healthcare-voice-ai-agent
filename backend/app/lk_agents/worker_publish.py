"""Publish TTS / lipsync UI payloads to the browser over LiveKit data channels."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from typing import Any

import httpx

from app.lk_agents.worker_env import (
    lipsync_before_room_audio,
    tts_ui_chunk_bytes,
    worker_lipsync_enabled,
)

logger = logging.getLogger(__name__)


async def publish_ui_payload(room: Any, dest_identity: str, payload: dict[str, Any]) -> None:
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


async def publish_lipsync_mp4_from_wav(
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
        await publish_ui_payload(
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
        await publish_ui_payload(
            room,
            dest_identity,
            {"kind": "lipsync_mp4_error", "rid": rid, "http": r.status_code},
        )
        return
    mp4 = r.content
    if not mp4:
        await publish_ui_payload(room, dest_identity, {"kind": "lipsync_mp4_error", "rid": rid})
        return
    chunk_sz = tts_ui_chunk_bytes()
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
        await publish_ui_payload(room, dest_identity, payload)
        seq += 1


async def publish_tts_wav_body(room: Any, dest_identity: str, wav: bytes, rid: str) -> None:
    """Chunked WAV payload after ``tts_begin`` (runs in background so room audio is not blocked)."""
    if not room or not dest_identity or not wav or not rid:
        return
    chunk_sz = tts_ui_chunk_bytes()
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
        await publish_ui_payload(room, dest_identity, payload)
        seq += 1


async def publish_tts_wav_chunks(
    room: Any,
    dest_identity: str,
    wav: bytes,
    api_base: str,
) -> None:
    """Send ``tts_begin`` (awaited), WAV chunks, and optionally worker-driven MuseTalk MP4 chunks."""
    if not room or not dest_identity or not wav:
        return
    rid = uuid.uuid4().hex[:16]
    worker_lip = worker_lipsync_enabled()
    begin: dict[str, Any] = {"kind": "tts_begin", "rid": rid}
    if worker_lip:
        begin["worker_lipsync"] = True
    await publish_ui_payload(room, dest_identity, begin)
    gate = lipsync_before_room_audio()
    if worker_lip and gate:
        await publish_lipsync_mp4_from_wav(room, dest_identity, wav, rid, api_base)
    if gate:
        await publish_tts_wav_body(room, dest_identity, wav, rid)
    else:
        asyncio.create_task(publish_tts_wav_body(room, dest_identity, wav, rid))
    if worker_lip and not gate:
        asyncio.create_task(publish_lipsync_mp4_from_wav(room, dest_identity, wav, rid, api_base))


async def publish_tts_segment_chunks(
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
    worker_lip = worker_lipsync_enabled()
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
    await publish_ui_payload(room, dest_identity, begin)
    gate = lipsync_before_room_audio()
    if worker_lip and gate:
        await publish_lipsync_mp4_from_wav(room, dest_identity, wav, rid, api_base)
    if gate:
        await publish_tts_wav_body(room, dest_identity, wav, rid)
    else:
        asyncio.create_task(publish_tts_wav_body(room, dest_identity, wav, rid))
    if worker_lip and not gate:
        asyncio.create_task(publish_lipsync_mp4_from_wav(room, dest_identity, wav, rid, api_base))
