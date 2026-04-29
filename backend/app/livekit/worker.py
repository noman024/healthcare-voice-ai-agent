"""Async LiveKit room agent: remote mic track → same finalize pipeline as WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from typing import Any

from app.conversation.finalize_audio import iter_finalize_batch_turn_events, strip_agent_event_for_data_transport
from app.livekit.protocol import (
    DEFAULT_AGENT_DATA_TOPIC,
    normalize_topic,
    parse_control_payload,
    summarize_control,
)

logger = logging.getLogger(__name__)

try:
    from livekit import rtc
except ImportError as e:
    rtc = None  # type: ignore[assignment]
    _IMPORT_ERR = e
else:
    _IMPORT_ERR = None


def _require_rtc() -> Any:
    if rtc is None:
        raise RuntimeError(
            "livekit package required: pip install -r requirements-livekit.txt"
        ) from _IMPORT_ERR
    return rtc


class LiveKitAgentWorker:
    """
    Connect as a dedicated **agent** participant, subscribe to the first remote **audio** track,
    buffer PCM frames while ``recording`` is true, and on ``finalize`` run
    :func:`~app.conversation.finalize_audio.iter_finalize_batch_turn_events`.

    Control plane uses the same JSON fields as ``/ws/conversation_audio`` (``action`` / ``session_id`` / …)
    sent on the reliable data channel (optional topic filter).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        livekit_url: str,
        agent_token: str,
        agent_identity: str,
        data_topic: str | None = None,
    ) -> None:
        _require_rtc()
        self._conn = conn
        self._url = livekit_url.rstrip("/")
        self._token = agent_token
        self._agent_identity = agent_identity.strip() or "agent-worker"
        self._data_topic = normalize_topic(data_topic) or DEFAULT_AGENT_DATA_TOPIC

        self._room: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._recording = False
        self._frames: list[Any] = []
        self._meta: dict[str, Any] = {}
        self._pump_task: asyncio.Task[None] | None = None
        self._disconnect_event = asyncio.Event()

    async def run(self) -> None:
        """Connect, process events until disconnect or failure."""
        R = _require_rtc()
        self._loop = asyncio.get_running_loop()
        self._room = R.Room()

        @self._room.on("track_subscribed")
        def _on_track(track: Any, _pub: Any, participant: Any) -> None:
            asyncio.create_task(self._handle_track_subscribed(track, participant))

        @self._room.on("data_received")
        def _on_data(packet: Any) -> None:
            asyncio.create_task(self._handle_data_packet(packet))

        @self._room.on("disconnected")
        def _on_dc(_reason: str) -> None:
            self._disconnect_event.set()

        logger.info("livekit_worker_connecting url=%s identity=%s", self._url, self._agent_identity)
        await self._room.connect(self._url, self._token)
        logger.info("livekit_worker_connected")

        await self._disconnect_event.wait()
        logger.info("livekit_worker_shutdown_cleanup")
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass

    async def _handle_track_subscribed(self, track: Any, participant: Any) -> None:
        R = _require_rtc()
        ident = str(getattr(participant, "identity", "") or "")
        if ident == self._agent_identity:
            return
        if track.kind != R.TrackKind.KIND_AUDIO:
            return
        if self._pump_task is not None and not self._pump_task.done():
            logger.info("livekit_worker_skip_extra_audio_track ident=%s", ident)
            return

        stream = R.AudioStream.from_track(track=track)
        self._pump_task = asyncio.create_task(self._pump_audio_stream(stream))
        logger.info("livekit_worker_audio_pump_started remote=%s", ident)

    async def _pump_audio_stream(self, stream: Any) -> None:
        try:
            async for ev in stream:
                frame = ev.frame
                async with self._lock:
                    if self._recording:
                        self._frames.append(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("livekit_worker_pump_failed")
        finally:
            try:
                await stream.aclose()
            except Exception:
                logger.debug("livekit_stream_aclose", exc_info=True)

    async def _handle_data_packet(self, packet: Any) -> None:
        raw_topic = normalize_topic(getattr(packet, "topic", None))
        if self._data_topic and raw_topic and raw_topic != self._data_topic:
            return

        verb, obj = parse_control_payload(packet.data)
        if verb == "ping":
            await self._publish_json({"type": "pong"})
            return
        if verb == "start":
            meta = summarize_control(obj)
            async with self._lock:
                self._frames.clear()
                self._meta = meta
                self._recording = True
            await self._publish_json({"type": "ready", "session_id": meta["session_id"]})
            return
        if verb == "finalize":
            async with self._lock:
                self._recording = False
                frames = list(self._frames)
                self._frames.clear()
                meta = dict(self._meta)
            await self._finalize_and_publish(frames, meta)
            return

    def _frames_to_wav(self, frames: list[Any]) -> bytes:
        R = _require_rtc()
        if not frames:
            return b""
        combined = R.combine_audio_frames(frames)
        return combined.to_wav_bytes()

    async def _finalize_and_publish(self, frames: list[Any], meta: dict[str, Any]) -> None:
        wav = await asyncio.to_thread(self._frames_to_wav, frames)
        session_id = str(meta.get("session_id") or "default")
        language = meta.get("language")
        return_speech = bool(meta.get("return_speech", True))
        ext = str(meta.get("file_extension") or ".wav")

        def _run_pipeline() -> list[dict[str, Any]]:
            return list(
                iter_finalize_batch_turn_events(
                    self._conn,
                    audio_bytes=wav,
                    file_suffix=ext,
                    session_id=session_id,
                    language=language,
                    return_speech=return_speech,
                    conversation_id=meta.get("conversation_id"),
                ),
            )

        try:
            events = await asyncio.to_thread(_run_pipeline)
        except Exception as e:
            logger.exception("livekit_worker_pipeline_failed")
            await self._publish_json({"type": "error", "message": str(e)})
            return

        for ev in events:
            if not isinstance(ev, dict):
                continue
            out = strip_agent_event_for_data_transport(ev)
            await self._publish_json(out)

    async def _publish_json(self, obj: dict[str, Any]) -> None:
        if self._room is None:
            return
        payload = json.dumps(obj, default=str).encode("utf-8")
        if len(payload) > 14_000:
            obj2 = strip_agent_event_for_data_transport(obj)
            payload = json.dumps(obj2, default=str).encode("utf-8")
        try:
            await self._room.local_participant.publish_data(
                payload,
                topic=self._data_topic or DEFAULT_AGENT_DATA_TOPIC,
                reliable=True,
            )
        except Exception:
            logger.exception("livekit_worker_publish_data_failed")


def default_agent_token(*, room: str, identity: str) -> str:
    """Mint JWT using the same env keys as :func:`app.livekit_tokens.try_build_livekit_token`."""
    from app.livekit_tokens import try_build_livekit_token

    r = try_build_livekit_token(room=room, identity=identity, name="Voice agent worker")
    return str(r["token"])


def load_worker_env() -> dict[str, str]:
    """Read env for ``scripts/livekit_agent_worker.py``."""
    url = os.getenv("LIVEKIT_URL", "ws://127.0.0.1:7880").strip()
    room = os.getenv("LIVEKIT_ROOM", "").strip()
    ident = os.getenv("LIVEKIT_AGENT_IDENTITY", "agent-worker").strip() or "agent-worker"
    topic = os.getenv("LIVEKIT_AGENT_DATA_TOPIC", DEFAULT_AGENT_DATA_TOPIC).strip()
    if not room:
        raise ValueError("LIVEKIT_ROOM is required (must match the browser room name).")
    return {"url": url, "room": room, "identity": ident, "topic": topic}


async def async_main(conn: sqlite3.Connection) -> None:
    """CLI entry — connect with env-loaded settings."""
    _require_rtc()
    cfg = load_worker_env()
    token = default_agent_token(room=cfg["room"], identity=cfg["identity"])
    worker = LiveKitAgentWorker(
        conn,
        livekit_url=cfg["url"],
        agent_token=token,
        agent_identity=cfg["identity"],
        data_topic=cfg["topic"],
    )
    await worker.run()
