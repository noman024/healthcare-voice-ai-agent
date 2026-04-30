"""LiveKit worker environment toggles (TTS chunking, lipsync, segmentation)."""

from __future__ import annotations

import os

_TTS_WAV_UI_CHUNK_DEFAULT = 24_000


def tts_ui_chunk_bytes() -> int:
    raw = os.getenv("VOICE_TTS_UI_CHUNK_BYTES", "").strip()
    if raw:
        return max(1024, int(raw))
    return _TTS_WAV_UI_CHUNK_DEFAULT


def worker_lipsync_enabled() -> bool:
    """POST Piper WAV to ``/avatar/lipsync`` from the worker as soon as TTS returns (before browser reassembles chunks)."""
    v = os.getenv("VOICE_WORKER_LIPSYNC", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def lipsync_before_room_audio() -> bool:
    """When True (default if worker lipsync on), await full MP4 publish before emitting room PCM so browser+room align like WebSocket+attachAudio."""
    if not worker_lipsync_enabled():
        return False
    v = os.getenv("VOICE_LIPSYNC_BEFORE_ROOM_AUDIO", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def livekit_tts_segmented() -> bool:
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
    return not worker_lipsync_enabled()
