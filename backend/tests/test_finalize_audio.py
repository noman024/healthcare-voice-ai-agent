"""Tests for finish-audio stripping utilities (no LiveKit required)."""

from __future__ import annotations

from app.conversation.finalize_audio import strip_agent_event_for_data_transport


def test_strip_agent_event_removes_large_audio_field():
    ev = strip_agent_event_for_data_transport(
        {"type": "done", "final_response": "hello", "audio_wav_base64": "AABBiV"},
    )
    assert "audio_wav_base64" not in ev
    assert ev.get("final_response") == "hello"

