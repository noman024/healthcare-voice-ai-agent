"""POST /agent/summary — empty transcript should 422."""

from app.agent.memory import clear_session_memory_for_tests


def test_agent_summary_no_history_returns_422(api_client):
    clear_session_memory_for_tests()
    r = api_client.post("/agent/summary", json={"session_id": "fresh-empty-session-xx"})
    assert r.status_code == 422


def test_session_transcript_helper():
    clear_session_memory_for_tests()
    from app.agent.memory import get_session_memory, get_session_transcript

    m = get_session_memory("trx")
    m.append_exchange("Hello", "Hi there")

    assert "user:" in get_session_transcript("trx").lower()
    assert "Hello" in get_session_transcript("trx")
