"""Speech-to-text (faster-whisper) and text-to-speech (Piper CLI) helpers."""

from app.audio.stt import get_whisper_model, transcribe_file, transcribe_path
from app.audio.tts import TTSError, is_tts_configured, synthesize_wav_bytes

__all__ = [
    "TTSError",
    "get_whisper_model",
    "is_tts_configured",
    "synthesize_wav_bytes",
    "transcribe_file",
    "transcribe_path",
]
