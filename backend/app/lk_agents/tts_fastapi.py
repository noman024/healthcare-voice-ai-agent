"""TTS via FastAPI ``POST /tts`` (Piper WAV) for a single LiveKit pipeline."""

from __future__ import annotations

import io
import wave

import httpx
from livekit import rtc
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid


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
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=publish_sample_rate,
            num_channels=1,
        )
        self._publish_sr = publish_sample_rate
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
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
        r = await self._http.post("/tts", json={"text": self.input_text})
        r.raise_for_status()
        wav = r.content
        pub_sr = self._piper._publish_sr
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

        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=pub_sr,
            num_channels=num_channels,
            mime_type="audio/pcm",
        )
        output_emitter.push(pcm)
        output_emitter.flush()

