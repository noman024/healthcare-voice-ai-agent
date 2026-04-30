"""TTS via FastAPI ``POST /tts`` (Piper WAV) for a single LiveKit pipeline."""

from __future__ import annotations

import io
import logging
import os
import wave
from collections.abc import Awaitable, Callable

import httpx
from livekit import rtc
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

from app.lk_agents.tts_segmentation import split_text_for_segmented_tts

logger = logging.getLogger(__name__)


def _resample_int16_pcm(
    pcm: bytes,
    *,
    src_sr: int,
    dst_sr: int,
    num_channels: int,
) -> bytes:
    """Resample interleaved int16 PCM; required when WAV rate != RoomIO publish rate."""
    if src_sr == dst_sr:
        return pcm
    resampler = rtc.AudioResampler(src_sr, dst_sr, num_channels=num_channels)
    raw = resampler.push(bytearray(pcm))
    out = bytearray()
    for frame in raw:
        out.extend(frame.data.tobytes())
    for frame in resampler.flush():
        out.extend(frame.data.tobytes())
    return bytes(out)


class FastApiPiperTTS(tts.TTS):
    """Piper WAV from ``/tts``; audio is normalized to ``publish_sample_rate`` for RoomIO."""

    def __init__(
        self,
        *,
        base_url: str,
        publish_sample_rate: int = 24_000,
        timeout_s: float = 120.0,
        on_original_wav: Callable[[bytes], Awaitable[None]] | None = None,
        on_segment_wav: Callable[[bytes, int, int, float, str], Awaitable[None]] | None = None,
        segmented: bool | None = None,
        max_segment_chars: int | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=publish_sample_rate,
            num_channels=1,
        )
        self._publish_sr = publish_sample_rate
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._on_original_wav = on_original_wav
        self._on_segment_wav = on_segment_wav
        if segmented is None:
            segmented = os.getenv("VOICE_TTS_SEGMENTED", "1").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        self._segmented = bool(segmented)
        msc = max_segment_chars
        if msc is None:
            try:
                msc = int((os.getenv("VOICE_TTS_MAX_SEGMENT_CHARS") or "180").strip() or "180")
            except ValueError:
                msc = 180
        self._max_segment_chars = max(40, min(int(msc), 600))
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=True,
        )

    @property
    def model(self) -> str:
        return "fastapi-piper"

    @property
    def provider(self) -> str:
        return "fastapi"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return _FastApiChunkedStream(
            tts=self, input_text=text, conn_options=conn_options, client=self._client
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class _FastApiChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: FastApiPiperTTS,
        input_text: str,
        conn_options: APIConnectOptions,
        client: httpx.AsyncClient,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._http = client
        self._piper: FastApiPiperTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        pub_sr = self._piper._publish_sr
        text = self.input_text.strip()
        if not text:
            return

        use_seg = self._piper._segmented and self._piper._on_segment_wav is not None
        if use_seg:
            segments = split_text_for_segmented_tts(text, max_chars=self._piper._max_segment_chars)
        else:
            segments = [text]

        utterance_id = shortuuid()
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=pub_sr,
            num_channels=1,
            mime_type="audio/pcm",
        )

        cumulative_samples = 0
        nseg = len(segments)
        combined_original: bytearray = bytearray()

        for idx, seg in enumerate(segments):
            r = await self._http.post("/tts", json={"text": seg})
            r.raise_for_status()
            wav = r.content
            combined_original.extend(wav)
            offset_ms = (cumulative_samples / float(pub_sr)) * 1000.0 if cumulative_samples else 0.0

            cb_seg = self._piper._on_segment_wav
            if cb_seg:
                try:
                    await cb_seg(wav, idx, nseg, offset_ms, utterance_id)
                except Exception:
                    logger.exception("on_segment_wav failed seg=%s", idx)

            with wave.open(io.BytesIO(wav), "rb") as wf:
                src_sr = wf.getframerate()
                num_channels = wf.getnchannels()
                sampw = wf.getsampwidth()
                if sampw != 2:
                    raise ValueError(f"Piper WAV must be 16-bit PCM; sampwidth={sampw}")
                pcm = wf.readframes(wf.getnframes())

            if src_sr != pub_sr:
                pcm = _resample_int16_pcm(
                    pcm, src_sr=src_sr, dst_sr=pub_sr, num_channels=num_channels
                )
            # Full-utterance fan-out (MuseTalk + ``va``) must finish before room PCM so lipsync MP4 and
            # TTS start together instead of “entire reply heard, then avatar animates”.
            if not use_seg:
                cb_full = self._piper._on_original_wav
                if cb_full:
                    try:
                        await cb_full(bytes(combined_original) if combined_original else wav)
                    except Exception:
                        logger.exception("on_original_wav failed (MuseTalk UI fan-out)")
            output_emitter.push(pcm)
            cumulative_samples += len(pcm) // (2 * num_channels)

        output_emitter.flush()

