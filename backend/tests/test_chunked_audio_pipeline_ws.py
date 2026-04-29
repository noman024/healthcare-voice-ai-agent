"""Chunked audio WS stream: stt_started → stt → agent events."""

from __future__ import annotations

import app.llm.ollama as ollama_mod
from app.conversation import pipeline as pipeline_mod


def test_iter_chunked_emits_stt_started_then_stt_then_done(db_conn, monkeypatch):
    monkeypatch.setattr(
        pipeline_mod,
        "transcribe_audio_bytes",
        lambda audio_bytes, suffix, language=None: ("hello world", "en"),
    )

    def fake_chat(messages, *, client=None, timeout_s=None, response_format=None):
        sys0 = (messages[0].get("content") or "") if messages else ""
        if "You finalize" in sys0 or "finalize" in sys0.lower():
            return "Done."
        return '{"intent":"t","tool":"none","arguments":{},"response":"draft"}'

    monkeypatch.setattr(ollama_mod, "ollama_chat", fake_chat)

    events = list(
        pipeline_mod.iter_chunked_audio_turn_events(
            db_conn,
            audio_bytes=b"fake",
            file_suffix=".wav",
            session_id="chunk-ws",
            language=None,
            return_speech=False,
        ),
    )
    types = [e.get("type") for e in events]
    assert types[0] == "stt_started"
    assert types[1] == "stt"
    assert events[1]["transcript"] == "hello world"
    assert isinstance(events[1].get("stt_elapsed_ms"), int)
    assert isinstance(events[1].get("audio_byte_len"), int)
    assert "done" in types
    done = next(e for e in events if e.get("type") == "done")
    assert done.get("transcript") == "hello world"
    assert done.get("mode") == "audio"
    assert isinstance(done.get("stt_elapsed_ms"), int)
    assert isinstance(done.get("audio_byte_len"), int)
