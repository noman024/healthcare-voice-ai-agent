"""Batch STT using the same faster-whisper path as FastAPI ``/stt``."""

from __future__ import annotations

import asyncio

from livekit import rtc
from livekit.agents import stt
from livekit.agents import utils
from livekit.agents.language import LanguageCode
from livekit.agents.stt import SpeechData, SpeechEvent, SpeechEventType, STTCapabilities
from livekit.agents.types import NOT_GIVEN, APIConnectOptions, NotGivenOr

from app.audio.bytes_stt import transcribe_audio_bytes


class FasterWhisperBatchSTT(stt.STT):
    """Non-streaming recognize(); use with ``StreamAdapter`` + Silero VAD for realtime turns."""

    def __init__(self) -> None:
        super().__init__(
            capabilities=STTCapabilities(
                streaming=False,
                interim_results=False,
                diarization=False,
                offline_recognize=True,
            )
        )

    @property
    def model(self) -> str:
        return "faster-whisper"

    @property
    def provider(self) -> str:
        return "local"

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> SpeechEvent:
        del conn_options  # unused
        combined: rtc.AudioFrame = (
            rtc.combine_audio_frames(buffer) if isinstance(buffer, list) else buffer
        )
        wav = combined.to_wav_bytes()
        lang = language if isinstance(language, str) and language.strip() else None

        def _run() -> tuple[str, str | None]:
            return transcribe_audio_bytes(wav, suffix=".wav", language=lang)

        text, detected = await asyncio.to_thread(_run)
        lang_code = (detected or lang or "en").strip() or "en"
        if len(lang_code) > 16:
            lang_code = lang_code[:16]
        lang_out = LanguageCode(lang_code)
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[SpeechData(language=lang_out, text=(text or "").strip())],
        )

    async def aclose(self) -> None:
        return None
