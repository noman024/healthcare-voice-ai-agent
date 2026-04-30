from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.audio.bytes_stt import transcribe_audio_bytes

router = APIRouter(tags=["audio"])


class TTSBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@router.post("/stt")
async def stt_endpoint(
    audio: UploadFile = File(...),
    language: str | None = Form(None),
) -> dict[str, str | None]:
    """
    Speech-to-text via faster-whisper. Send multipart form: field `audio` (file),
    optional field `language` (ISO-639-1, e.g. en).
    """
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


@router.post("/tts")
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
