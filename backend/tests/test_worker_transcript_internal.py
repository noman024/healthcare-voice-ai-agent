"""POST /internal/voice/worker/transcript — LiveKit worker mirror into SQLite."""

from __future__ import annotations

import app.llm.ollama as ollama_mod

from app.agent.memory import clear_session_memory_for_tests


def test_worker_transcript_route_missing_secret_returns_404(api_client, monkeypatch):
    monkeypatch.delenv("VOICE_INTERNAL_SECRET", raising=False)
    r = api_client.post(
        "/internal/voice/worker/transcript",
        json={"conversation_id": "c1", "role": "user", "content": "hi"},
        headers={"X-Voice-Internal": "nope"},
    )
    assert r.status_code == 404


def test_worker_transcript_forbidden_wrong_header(api_client, monkeypatch):
    monkeypatch.setenv("VOICE_INTERNAL_SECRET", "correct")
    r = api_client.post(
        "/internal/voice/worker/transcript",
        json={"conversation_id": "c1", "role": "user", "content": "hi"},
        headers={"X-Voice-Internal": "wrong"},
    )
    assert r.status_code == 403


def test_worker_transcript_persists_and_summary_reads(api_client, monkeypatch):
    monkeypatch.setenv("VOICE_INTERNAL_SECRET", "sekrit")
    monkeypatch.delenv("CONVERSATION_PERSIST", raising=False)
    r = api_client.post(
        "/internal/voice/worker/transcript",
        json={"conversation_id": "room-voice", "role": "user", "content": "Book Tuesday"},
        headers={"X-Voice-Internal": "sekrit"},
    )
    assert r.status_code == 200
    r2 = api_client.post(
        "/internal/voice/worker/transcript",
        json={"conversation_id": "room-voice", "role": "assistant", "content": "Which date?"},
        headers={"X-Voice-Internal": "sekrit"},
    )
    assert r2.status_code == 200

    clear_session_memory_for_tests()
    monkeypatch.setattr(
        ollama_mod,
        "ollama_chat",
        lambda *a, **k: '{"narrative":"Caller asked to book.","user_preferences":["weekday"]}',
    )
    s = api_client.post(
        "/agent/summary",
        json={"session_id": "default", "conversation_id": "room-voice"},
    )
    assert s.status_code == 200
    body = s.json()
    assert body["conversation_id"] == "room-voice"
    assert body["summary"] == "Caller asked to book."
    assert body["user_preferences"] == ["weekday"]
