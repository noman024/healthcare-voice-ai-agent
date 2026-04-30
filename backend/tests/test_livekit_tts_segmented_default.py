"""Defaults for LiveKit worker sentence-scale TTS (segmentation vs one chunk per utterance)."""

from app.lk_agents.voice_agent import _livekit_tts_segmented


def test_segmented_explicit_on_even_with_worker_lipsync(monkeypatch):
    monkeypatch.setenv("VOICE_TTS_SEGMENTED", "1")
    monkeypatch.setenv("VOICE_WORKER_LIPSYNC", "1")
    assert _livekit_tts_segmented() is True


def test_segmented_explicit_off(monkeypatch):
    monkeypatch.setenv("VOICE_TTS_SEGMENTED", "0")
    monkeypatch.setenv("VOICE_WORKER_LIPSYNC", "1")
    assert _livekit_tts_segmented() is False


def test_segmented_default_off_when_worker_lipsync_on(monkeypatch):
    monkeypatch.delenv("VOICE_TTS_SEGMENTED", raising=False)
    monkeypatch.setenv("VOICE_WORKER_LIPSYNC", "1")
    assert _livekit_tts_segmented() is False


def test_segmented_default_on_when_worker_lipsync_off(monkeypatch):
    monkeypatch.delenv("VOICE_TTS_SEGMENTED", raising=False)
    monkeypatch.setenv("VOICE_WORKER_LIPSYNC", "0")
    assert _livekit_tts_segmented() is True
