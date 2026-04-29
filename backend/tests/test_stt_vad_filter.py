"""WHISPER_VAD_FILTER toggles faster-whisper vad_filter on transcribe."""

from __future__ import annotations

import app.audio.stt as stt_mod


def test_transcribe_path_omits_vad_filter_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("WHISPER_VAD_FILTER", raising=False)
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, audio: str, **kwargs: object) -> tuple[object, object]:
            captured.clear()
            captured.update(kwargs)

            class Seg:
                text = ""

            class Info:
                duration = 0.5
                language = "en"

            return iter([Seg()]), Info()

    monkeypatch.setattr(stt_mod, "get_whisper_model", lambda: FakeModel())
    monkeypatch.setattr(stt_mod, "reset_whisper_model", lambda: None)

    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"x")

    text, lang = stt_mod.transcribe_path(wav, language=None)
    assert text == ""
    assert lang == "en"
    assert captured.get("vad_filter") is not True


def test_transcribe_path_sets_vad_filter_when_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WHISPER_VAD_FILTER", "1")
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, audio: str, **kwargs: object) -> tuple[object, object]:
            captured.clear()
            captured.update(kwargs)

            class Seg:
                text = " hi"

            class Info:
                duration = 0.5
                language = "en"

            return iter([Seg()]), Info()

    monkeypatch.setattr(stt_mod, "get_whisper_model", lambda: FakeModel())
    monkeypatch.setattr(stt_mod, "reset_whisper_model", lambda: None)

    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"x")

    text, lang = stt_mod.transcribe_path(wav, language=None)
    assert text.strip() == "hi"
    assert lang == "en"
    assert captured.get("vad_filter") is True
