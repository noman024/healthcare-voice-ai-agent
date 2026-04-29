"""POST /agent/summary — empty transcript should 422; hydration from SQLite when persist on."""

import sqlite3

import app.llm.ollama as ollama_mod

from app.agent.memory import clear_session_memory_for_tests, get_session_memory, get_session_transcript


def test_agent_summary_no_history_returns_422(api_client):
    clear_session_memory_for_tests()
    r = api_client.post("/agent/summary", json={"session_id": "fresh-empty-session-xx"})
    assert r.status_code == 422


def test_agent_summary_hydrates_from_sqlite(api_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATION_PERSIST", "1")

    db_path = tmp_path / "api.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        ("sess-h", "user", "hi"),
    )
    conn.execute(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        ("sess-h", "assistant", "hello"),
    )
    conn.commit()
    conn.close()

    clear_session_memory_for_tests()

    def fake_chat(messages, **kwargs):
        return '{"narrative":"One line summary","user_preferences":["morning slots"]}'

    monkeypatch.setattr(ollama_mod, "ollama_chat", fake_chat)

    r = api_client.post("/agent/summary", json={"session_id": "sess-h"})
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "sess-h"
    assert data["conversation_id"] == "sess-h"
    assert data["summary"] == "One line summary"
    assert data["user_preferences"] == ["morning slots"]
    assert "generated_at" in data and "T" in data["generated_at"]
    assert data["appointments"] == []


def test_agent_summary_includes_appointments_for_phone(api_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATION_PERSIST", "1")
    db_path = tmp_path / "api.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO appointments (name, phone, date, time, status)
        VALUES ('Pat', '+15551234567', '2026-05-01', '10:00', 'booked')
        """,
    )
    conn.execute(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        ("sess-p", "user", "book me"),
    )
    conn.execute(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        ("sess-p", "assistant", "sure"),
    )
    conn.commit()
    conn.close()

    clear_session_memory_for_tests()

    monkeypatch.setattr(
        ollama_mod,
        "ollama_chat",
        lambda *a, **k: '{"narrative":"ok","user_preferences":[]}',
    )

    r = api_client.post(
        "/agent/summary",
        json={"session_id": "sess-p", "phone": "+1 555 123 4567"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["phone"] == "+15551234567"
    assert len(data["appointments"]) == 1
    assert data["appointments"][0]["date"] == "2026-05-01"


def test_agent_summary_loads_transcript_by_conversation_id(api_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATION_PERSIST", "1")
    db_path = tmp_path / "api.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        ("room-x", "user", "hello room"),
    )
    conn.commit()
    conn.close()
    clear_session_memory_for_tests()
    monkeypatch.setattr(
        ollama_mod,
        "ollama_chat",
        lambda *a, **k: '{"narrative":"short","user_preferences":[]}',
    )
    r = api_client.post(
        "/agent/summary",
        json={"session_id": "+15550001111", "conversation_id": "room-x"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["conversation_id"] == "room-x"
    assert body["session_id"] == "+15550001111"
    assert body["summary"] == "short"
def test_session_transcript_helper():
    clear_session_memory_for_tests()
    m = get_session_memory("trx")
    m.append_exchange("Hello", "Hi there")

    assert "user:" in get_session_transcript("trx").lower()
    assert "Hello" in get_session_transcript("trx")
