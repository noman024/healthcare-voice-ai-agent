import json

import httpx

from app.agent.memory import clear_session_memory_for_tests, get_session_memory
from app.agent.runner import run_turn


def test_memory_keeps_last_twenty_chat_messages():
    clear_session_memory_for_tests()
    mem = get_session_memory("roll")
    for i in range(15):
        mem.append_exchange(f"u{i}", f"a{i}")
    msgs = mem.as_ollama_messages()
    assert len(msgs) == 20
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "u5"


def test_run_turn_planner_finalize_mock(db_conn):
    clear_session_memory_for_tests()
    n = {"c": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["c"] += 1
        if n["c"] == 1:
            content = '{"intent":"greet","tool":"none","arguments":{},"response":"Hello draft"}'
        else:
            content = "Hello! I can help you schedule a healthcare appointment."
        return httpx.Response(200, json={"message": {"content": content}})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        out = run_turn(
            db_conn,
            user_message="Hi",
            session_id="s-mock",
            client=client,
        )
    assert out["plan"]["tool"] == "none"
    assert "appointment" in out["final_response"].lower()
    assert n["c"] == 2


def test_run_turn_executes_booking_tool(db_conn):
    clear_session_memory_for_tests()
    n = {"c": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["c"] += 1
        payload = json.loads(request.content.decode())
        last_user = ""
        for msg in reversed(payload.get("messages", [])):
            if msg.get("role") == "user":
                last_user = msg.get("content", "")
                break
        if n["c"] == 1:
            plan = {
                "intent": "book",
                "tool": "book_appointment",
                "arguments": {
                    "name": "Jamie",
                    "phone": "+15559876543",
                    "date": "2026-08-01",
                    "time": "10:00",
                },
                "response": "Booking that now.",
            }
            content = json.dumps(plan)
        else:
            assert "tool_execution" in last_user or "Jamie" in last_user
            content = "You are booked on August 1 at 10 AM. Anything else?"
        return httpx.Response(200, json={"message": {"content": content}})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        out = run_turn(
            db_conn,
            user_message="Book Jamie at 10 on Aug 1, phone +1 555 987 6543",
            session_id="s-book",
            client=client,
        )

    assert out["tool_execution"] is not None
    assert out["tool_execution"]["success"] is True
    assert out["tool_execution"]["data"]["appointment"]["name"] == "Jamie"


def test_run_turn_identify_user_sets_session_identity(db_conn):
    clear_session_memory_for_tests()
    n = {"c": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["c"] += 1
        if n["c"] == 1:
            plan = {
                "intent": "identify",
                "tool": "identify_user",
                "arguments": {"phone": "+15551234567", "name": "Pat"},
                "response": "Thanks.",
            }
            content = json.dumps(plan)
        else:
            content = "Welcome, Pat. How can I help?"
        return httpx.Response(200, json={"message": {"content": content}})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        out = run_turn(
            db_conn,
            user_message="Pat here, +1 555 123 4567",
            session_id="anon",
            client=client,
        )
    assert out.get("session_identity") == {"suggested_session_id": "+15551234567"}


def test_run_turn_planner_exhaustion_falls_back(db_conn):
    """After 3 invalid planner JSON payloads, runner returns graceful plan + finalizer."""
    clear_session_memory_for_tests()
    n = {"c": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["c"] += 1
        if n["c"] <= 3:
            return httpx.Response(200, json={"message": {"content": "not-valid-json {{{"}})
        return httpx.Response(
            200,
            json={"message": {"content": "Could you briefly repeat what you'd like me to schedule?"}},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        out = run_turn(
            db_conn,
            user_message="ambiguous cancel blah",
            session_id="fallback-sess",
            client=client,
        )
    assert n["c"] == 4
    assert out["plan"]["intent"] == "planner_exhausted"
    assert out["plan"]["tool"] == "none"
    assert out["warning"] is not None
    assert isinstance(out["final_response"], str) and len(out["final_response"]) > 5
