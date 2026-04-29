from __future__ import annotations

import httpx


def test_health_llm_success(monkeypatch, api_client):
    import app.main as main_mod

    def fake_probe(base_url: str):
        req = httpx.Request("GET", f"{base_url}/api/tags")
        return httpx.Response(200, json={"models": []}, request=req)

    monkeypatch.setattr(main_mod, "_ollama_tags_get", fake_probe)
    r = api_client.get("/health/llm")
    assert r.status_code == 200
    assert r.json()["ollama"] == "ok"


def test_health_llm_unavailable(monkeypatch, api_client):
    import app.main as main_mod

    def fake_probe(base_url: str):
        raise httpx.ConnectError("refused", request=None)

    monkeypatch.setattr(main_mod, "_ollama_tags_get", fake_probe)
    r = api_client.get("/health/llm")
    assert r.status_code == 503
    assert r.json()["ollama"] == "unavailable"


def test_stt_mocked_transcribe(api_client, monkeypatch):
    def fake_transcribe(path, language=None):
        return "book Tuesday at three", "en"

    monkeypatch.setattr("app.audio.bytes_stt.transcribe_path", fake_transcribe)
    r = api_client.post(
        "/stt",
        files={"audio": ("clip.wav", b"dummy-bytes", "audio/wav")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "book Tuesday at three"
    assert body["language"] == "en"


def test_stt_rejects_empty_file(api_client, monkeypatch):
    monkeypatch.setattr(
        "app.audio.bytes_stt.transcribe_path",
        lambda *a, **k: ("should-not-run", "en"),
    )
    r = api_client.post(
        "/stt",
        files={"audio": ("empty.wav", b"", "audio/wav")},
    )
    assert r.status_code == 422


def test_tts_returns_503_when_unconfigured(api_client, monkeypatch):
    monkeypatch.delenv("PIPER_VOICE", raising=False)
    r = api_client.post("/tts", json={"text": "Hello there."})
    assert r.status_code == 503
