from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.tools.executor import TOOL_NAMES, execute_tool


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "tools_api.db"))
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_tool_names_complete():
    expected = {
        "identify_user",
        "fetch_slots",
        "book_appointment",
        "retrieve_appointments",
        "cancel_appointment",
        "modify_appointment",
        "end_conversation",
    }
    assert TOOL_NAMES == expected


def test_identify_user_validation(db_conn):
    r = execute_tool(db_conn, "identify_user", {})
    assert r["success"] is False
    assert r["error"]["code"] == "validation_error"

    r2 = execute_tool(db_conn, "identify_user", {"phone": "+1 555 123 4567", "name": "Pat"})
    assert r2["success"] is True
    assert r2["data"]["phone"] == "+15551234567"

    uk = execute_tool(db_conn, "identify_user", {"phone": "+44 7700 900123"})
    assert uk["success"] is True
    assert uk["data"]["phone"] == "+447700900123"

    too_short = execute_tool(db_conn, "identify_user", {"phone": "+49 123"})
    assert too_short["success"] is False


def test_fetch_slots_and_book_flow(db_conn):
    date = "2026-06-01"
    slots = execute_tool(db_conn, "fetch_slots", {"date": date})
    assert slots["success"] is True
    assert "09:00" in slots["data"]["available_slots"]

    b = execute_tool(
        db_conn,
        "book_appointment",
        {"name": "A", "phone": "+15550001111", "date": date, "time": "09:00"},
    )
    assert b["success"] is True

    slots2 = execute_tool(db_conn, "fetch_slots", {"date": date})
    assert "09:00" not in slots2["data"]["available_slots"]

    dbl = execute_tool(
        db_conn,
        "book_appointment",
        {"name": "B", "phone": "+15550002222", "date": date, "time": "09:00"},
    )
    assert dbl["success"] is False
    assert dbl["error"]["code"] == "double_booking"


def test_cancel_modify_retrieve(client):
    date = "2026-06-02"
    b = client.post(
        "/tools/invoke",
        json={
            "tool": "book_appointment",
            "arguments": {
                "name": "Sam",
                "phone": "(555) 000-3333",
                "date": date,
                "time": "10:30",
            },
        },
    )
    assert b.status_code == 200
    body = b.json()
    assert body["success"] is True
    appt_id = body["data"]["appointment"]["id"]

    lst = client.post(
        "/tools/invoke",
        json={"tool": "retrieve_appointments", "arguments": {"phone": "555-000-3333"}},
    )
    assert lst.status_code == 200
    assert len(lst.json()["data"]["appointments"]) == 1

    mod = client.post(
        "/tools/invoke",
        json={
            "tool": "modify_appointment",
            "arguments": {
                "appointment_id": appt_id,
                "phone": "+15550003333",
                "new_date": date,
                "new_time": "11:00",
            },
        },
    )
    assert mod.status_code == 200
    assert mod.json()["success"] is False

    mod_ok = client.post(
        "/tools/invoke",
        json={
            "tool": "modify_appointment",
            "arguments": {
                "appointment_id": appt_id,
                "phone": "5550003333",
                "new_date": "2026-06-03",
                "new_time": "14:00",
            },
        },
    )
    assert mod_ok.status_code == 200
    assert mod_ok.json()["success"] is True

    can = client.post(
        "/tools/invoke",
        json={
            "tool": "cancel_appointment",
            "arguments": {"appointment_id": appt_id, "phone": "5550003333"},
        },
    )
    assert can.status_code == 200
    assert can.json()["data"]["appointment"]["status"] == "cancelled"

    end = client.post(
        "/tools/invoke",
        json={"tool": "end_conversation", "arguments": {"reason": "done"}},
    )
    assert end.status_code == 200
    assert end.json()["data"]["ended"] is True


def test_unknown_tool(db_conn):
    r = execute_tool(db_conn, "not_a_tool", {})
    assert r["success"] is False
    assert r["error"]["code"] == "unknown_tool"
