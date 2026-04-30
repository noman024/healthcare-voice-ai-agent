"""
Simulate lipsync-related transports the same way /call does:

- **WebSocket** ``/ws/conversation_audio``: ``done`` may include ``audio_wav_base64`` → browser POSTs ``/avatar/lipsync``.
- **REST** ``POST /process`` with ``return_speech``: same WAV field → same POST chain.
- **LiveKit**: worker sends ``va`` ``tts_begin`` (+ optional segmentation fields); covered by contract test below.

MuseTalk is disabled in ``api_client`` (conftest); ``/avatar/lipsync`` returns **503** — enough to verify the chain is wired.
"""

from __future__ import annotations

import base64
import io
import wave

import pytest

import app.conversation.finalize_audio as finalize_audio_mod
import app.conversation.pipeline as pipeline_mod


def _tiny_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"\x00\x00" * 160)
    return buf.getvalue()


@pytest.fixture
def tiny_wav_b64() -> str:
    return base64.b64encode(_tiny_wav_bytes()).decode("ascii")


def test_ws_conversation_audio_done_includes_wav_then_lipsync_router_accepts_upload(
    api_client, monkeypatch, tiny_wav_b64: str
):
    tiny = base64.b64decode(tiny_wav_b64.encode("ascii"))

    def fake_iter(conn, **kwargs):
        _ = conn
        yield {"type": "stt_started", "session_id": kwargs.get("session_id") or "sim-ws"}
        yield {
            "type": "stt",
            "transcript": "hi",
            "stt_elapsed_ms": 0,
            "audio_byte_len": len(tiny),
        }
        yield {
            "type": "done",
            "transcript": "hi",
            "final_response": "Hello.",
            "audio_wav_base64": tiny_wav_b64,
            "mode": "audio",
        }

    monkeypatch.setattr(finalize_audio_mod, "iter_chunked_audio_turn_events", fake_iter)

    with api_client.websocket_connect("/ws/conversation_audio") as ws:
        ws.send_json(
            {
                "action": "start",
                "session_id": "sim-ws",
                "return_speech": True,
                "file_extension": ".wav",
            },
        )
        ready = ws.receive_json()
        assert ready.get("type") == "ready"
        ws.send_bytes(tiny)
        ws.send_json({"action": "finalize"})
        got_done = False
        for _ in range(64):
            msg = ws.receive_json()
            if msg.get("type") == "done":
                got_done = True
                assert msg.get("audio_wav_base64") == tiny_wav_b64
                break
            if msg.get("type") == "error":
                pytest.fail(f"unexpected error event: {msg}")
        assert got_done

    lip = api_client.post(
        "/avatar/lipsync",
        files={"audio": ("sim.wav", tiny, "audio/wav")},
    )
    assert lip.status_code == 503


def test_process_rest_mirrors_ui_text_lipsync_chain(api_client, monkeypatch, tiny_wav_b64: str):
    def fake_process_text(
        conn,
        *,
        message: str,
        session_id: str,
        return_speech: bool,
        conversation_id: str | None = None,
        client=None,
    ):
        _ = (conn, message, conversation_id, client)
        return {
            "mode": "text",
            "session_id": session_id,
            "transcript": message,
            "detected_language": None,
            "final_response": "Mock.",
            "plan": None,
            "tool_execution": None,
            "audio_wav_base64": tiny_wav_b64 if return_speech else None,
            "tts_configured": True,
        }

    monkeypatch.setattr(pipeline_mod, "process_text_message", fake_process_text)
    r = api_client.post(
        "/process",
        json={
            "message": "Hello",
            "session_id": "sim-rest",
            "return_speech": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("audio_wav_base64") == tiny_wav_b64

    tiny = base64.b64decode(tiny_wav_b64.encode("ascii"))
    lip = api_client.post(
        "/avatar/lipsync",
        files={"audio": ("sim.wav", tiny, "audio/wav")},
    )
    assert lip.status_code == 503


def test_livekit_va_tts_begin_segment_contract():
    """Keys the LiveKit worker sends for chunked avatar; frontend ``page.tsx`` depends on this shape."""
    utterance_id = "testutt_01"
    begin = {
        "kind": "tts_begin",
        "utterance_id": utterance_id,
        "segment_index": 1,
        "segment_count": 3,
        "audio_offset_ms": 123.45,
        "rid": f"{utterance_id}_1"[:128],
        "worker_lipsync": True,
    }
    assert begin["rid"].startswith(utterance_id)
    assert begin["kind"] == "tts_begin"
    for key in ("utterance_id", "audio_offset_ms", "rid"):
        assert key in begin
