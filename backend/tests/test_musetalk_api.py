"""MuseTalk HTTP helpers (no GPU in CI)."""

from __future__ import annotations


def test_avatar_lipsync_status_disabled(api_client, monkeypatch):
    monkeypatch.delenv("MUSETALK_ENABLED", raising=False)
    r = api_client.get("/avatar/lipsync/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False


def test_avatar_lipsync_post_disabled_503(api_client, monkeypatch):
    monkeypatch.delenv("MUSETALK_ENABLED", raising=False)
    r = api_client.post("/avatar/lipsync", files={"audio": ("a.wav", b"RIFF", "audio/wav")})
    assert r.status_code == 503


def test_avatar_lipsync_post_not_ready_503(api_client, monkeypatch):
    monkeypatch.setenv("MUSETALK_ENABLED", "1")
    monkeypatch.setenv("MUSETALK_ROOT", "/nonexistent/musetalk")
    r = api_client.post("/avatar/lipsync", files={"audio": ("a.wav", b"x", "audio/wav")})
    assert r.status_code == 503
