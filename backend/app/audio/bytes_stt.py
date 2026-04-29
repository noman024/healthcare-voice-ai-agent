"""Transcribe variable-length audio from in-memory bytes (shared by REST and WebSocket ingest)."""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.audio.stt import transcribe_path

logger = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = frozenset(
    {".wav", ".webm", ".mp3", ".ogg", ".flac", ".m4a", ".mp4", ".bin"},
)


def normalize_suffix(filename_or_ext: str | None) -> str:
    """Pick a temp-file suffix Whisper/ffmpeg can handle (.webm for browser MediaRecorder)."""
    if not filename_or_ext:
        return ".webm"
    s = filename_or_ext.strip().lower()
    if not s.startswith("."):
        s = Path(s).suffix.lower() or ".webm"
    if s not in _ALLOWED_SUFFIXES:
        return ".webm"
    return s


def transcribe_audio_bytes(
    data: bytes,
    *,
    suffix: str,
    language: str | None = None,
) -> tuple[str, str | None]:
    """
    Write ``data`` to a temporary file and run ``transcribe_path``.
    Returns (text, detected_language_or_none). Empty input yields ("", None).
    """
    if not data:
        return "", None
    suf = normalize_suffix(suffix)
    with NamedTemporaryFile(delete=False, suffix=suf) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return transcribe_path(tmp_path, language=language)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
