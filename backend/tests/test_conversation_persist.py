"""Optional SQLite hydration for rolling session transcripts."""

from __future__ import annotations

import json

import pytest

import app.llm.ollama as ollama_mod
from app.agent.memory import SessionMemory, clear_session_memory_for_tests
from app.db.conversation_messages import hydrate_session_memory, persist_exchange


@pytest.fixture(autouse=True)
def _persist_cleanup(db_conn):
    yield
    clear_session_memory_for_tests()


def test_persist_exchange_and_hydrate(db_conn, monkeypatch):
    monkeypatch.setenv("CONVERSATION_PERSIST", "1")

    persist_exchange(db_conn, session_id="p1", user_message="hi", assistant_message="bye")

    m = SessionMemory()
    hydrate_session_memory(m, db_conn, "p1")

    msgs = list(m.as_ollama_messages())
    assert msgs == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "bye"}]


def test_runner_hydrates_from_db_between_turns(db_conn, monkeypatch):
    monkeypatch.setenv("CONVERSATION_PERSIST", "1")
    planners: list[str] = []

    def fake_chat(messages, *, client=None, timeout_s=None, response_format=None, model=None):
        if response_format == "json":
            planners.append(json.dumps(messages))
        return (
            '{"intent":"t","tool":"none","arguments":{},"response":"draft"}'
            if response_format == "json"
            else "Assistant reply."
        )

    monkeypatch.setattr(ollama_mod, "ollama_chat", fake_chat)

    from app.agent.runner import run_turn

    run_turn(db_conn, user_message="first user line", session_id="reuse-sid")
    clear_session_memory_for_tests()

    run_turn(db_conn, user_message="second line", session_id="reuse-sid")

    assert len(planners) >= 2
    assert "first user line" in planners[-1]


def test_conversation_id_merges_transcript_across_agent_session_ids(db_conn, monkeypatch):
    """Stable conversation_id persists under one key while session_id can change (e.g. phone handoff)."""
    monkeypatch.setenv("CONVERSATION_PERSIST", "1")

    def fake_chat(messages, *, client=None, timeout_s=None, response_format=None, model=None):
        if response_format == "json":
            return '{"intent":"t","tool":"none","arguments":{},"response":"draft"}'
        return "Assistant reply."

    monkeypatch.setattr(ollama_mod, "ollama_chat", fake_chat)

    from app.agent.runner import run_turn

    run_turn(
        db_conn,
        user_message="hello",
        session_id="label-a",
        persistence_session_id="room-1",
    )
    clear_session_memory_for_tests()
    run_turn(
        db_conn,
        user_message="follow up",
        session_id="+15550009999",
        persistence_session_id="room-1",
    )

    m = SessionMemory()
    hydrate_session_memory(m, db_conn, "room-1")
    text = "\n".join(str(msg.get("content", "")) for msg in m.as_ollama_messages())
    assert "hello" in text and "follow up" in text


def test_turn_prunes_extra_rows(db_conn, monkeypatch):
    monkeypatch.setenv("CONVERSATION_PERSIST", "1")
    monkeypatch.setenv("CONVERSATION_PERSIST_MAX_MESSAGES", "1")

    persist_exchange(db_conn, session_id="trim", user_message="a", assistant_message="b")
    persist_exchange(db_conn, session_id="trim", user_message="c", assistant_message="d")

    n = int(
        db_conn.execute("SELECT COUNT(*) FROM conversation_messages WHERE session_id='trim'").fetchone()[
            0
        ],
    )
    assert n == 2
