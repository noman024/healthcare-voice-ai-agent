"""Tests for Phase 6 /process and /conversation (mock LLM + STT)."""

from __future__ import annotations


def test_process_with_mocks(api_client, monkeypatch):
    import app.llm.ollama as ollama_mod

    def fake_chat(messages, *, client=None, timeout_s=None, response_format=None, model=None):
        sys0 = (messages[0].get("content") or "") if messages else ""
        if "finalize" in sys0.lower() or "You finalize" in sys0:
            return "All set. Reply only."
        return '{"intent":"x","tool":"none","arguments":{},"response":"draft"}'

    monkeypatch.setattr(ollama_mod, "ollama_chat", fake_chat)

    r = api_client.post(
        "/process",
        json={"message": "Hi", "session_id": "p2", "return_speech": False},
    )
    assert r.status_code == 200
    b = r.json()
    assert b["mode"] == "text"
    assert "final_response" in b
    assert b["transcript"] == "Hi"


def test_conversation_multipart_message_mocked(api_client, monkeypatch):
    import app.llm.ollama as ollama_mod

    def fake_chat(messages, *, client=None, timeout_s=None, response_format=None, model=None):
        sys0 = (messages[0].get("content") or "") if messages else ""
        if "You finalize" in sys0:
            return "Done."
        return '{"intent":"x","tool":"none","arguments":{},"response":"d"}'

    monkeypatch.setattr(ollama_mod, "ollama_chat", fake_chat)

    r = api_client.post(
        "/conversation",
        data={"message": "Book help", "session_id": "c1", "return_speech": "false"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == "Book help"


def test_conversation_audio_uses_stt_mock(api_client, monkeypatch, tmp_path):
    import app.audio.bytes_stt as bytes_stt_mod
    import app.llm.ollama as ollama_mod

    monkeypatch.setattr(
        bytes_stt_mod,
        "transcribe_path",
        lambda p, language=None: ("user said test", "en"),
    )

    def fake_chat(messages, *, client=None, timeout_s=None, response_format=None, model=None):
        if "You finalize" in (messages[0].get("content") or ""):
            return "OK."
        return '{"intent":"x","tool":"none","arguments":{},"response":"d"}'

    monkeypatch.setattr(ollama_mod, "ollama_chat", fake_chat)

    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 100)
    with wav.open("rb") as f:
        r = api_client.post(
            "/conversation",
            files={"audio": ("clip.wav", f, "audio/wav")},
            data={"session_id": "c2", "return_speech": "false"},
        )
    assert r.status_code == 200
    assert r.json()["transcript"] == "user said test"


def test_conversation_requires_audio_or_message(api_client):
    r = api_client.post("/conversation", data={})
    assert r.status_code == 422
