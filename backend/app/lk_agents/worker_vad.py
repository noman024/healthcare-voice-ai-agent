"""Silero VAD configuration for the LiveKit voice worker."""

from __future__ import annotations

import os

from livekit.plugins import silero


def load_livekit_vad() -> silero.VAD:
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
