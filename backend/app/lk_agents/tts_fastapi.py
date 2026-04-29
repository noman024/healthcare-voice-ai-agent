"""TTS via FastAPI ``POST /tts`` (Piper WAV) for a single LiveKit pipeline."""

from __future__ import annotations

import io
import wave

import httpx
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid


class FastApiPiperTTS(tts.TTS):
    def __init__(self, *, base_url: str, timeout_s: float = 120.0) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=22050,
            num_channels=1,
        )
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
        with wave.open(io.BytesIO(wav), "rb") as wf:
            sample_rate = wf.getframerate()
            num_channels = wf.getnchannels()
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=sample_rate,
            num_channels=num_channels,
            mime_type="audio/wav",
        )
        output_emitter.push(wav)
        output_emitter.flush()

