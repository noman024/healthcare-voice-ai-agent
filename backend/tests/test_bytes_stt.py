from app.audio.bytes_stt import normalize_suffix, transcribe_audio_bytes


def test_normalize_suffix_defaults():
    assert normalize_suffix("") == ".webm"
    assert normalize_suffix(".wav") == ".wav"
    assert normalize_suffix("weird") == ".webm"


def test_transcribe_audio_bytes_empty():
    text, lang = transcribe_audio_bytes(b"", suffix=".wav", language=None)
    assert text == ""
    assert lang is None
