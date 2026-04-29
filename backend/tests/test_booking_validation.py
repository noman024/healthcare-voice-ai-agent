"""Hard validation: clinic slot grid, display names, session booking gate (+ identify + fetch)."""

from __future__ import annotations

from app.session_booking_gate import (
    clear_booking_gate_for_tests,
    register_offered_slots,
    register_verified_phone,
)
from app.tools.executor import (
    TOOL_BOOK_APPOINTMENT,
    TOOL_FETCH_SLOTS,
    TOOL_MODIFY_APPOINTMENT,
    execute_tool,
)


def test_book_rejects_time_outside_template_grid(db_conn):
    clear_booking_gate_for_tests()
    r = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "Pat Lee",
            "phone": "+15550001111",
            "date": "2026-08-20",
            "time": "17:30",
        },
    )
    assert r["success"] is False
    assert r["error"]["code"] == "validation_error"
    assert "clinic" in r["error"]["message"].lower() or "slot" in r["error"]["message"].lower()


def test_book_rejects_placeholder_name(db_conn):
    r = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "nothing",
            "phone": "+15550001111",
            "date": "2026-08-20",
            "time": "10:00",
        },
    )
    assert r["success"] is False
    assert r["error"]["field"] == "name"


def test_book_rejects_no1_pattern_name(db_conn):
    r = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "no.1",
            "phone": "+15550001111",
            "date": "2026-08-20",
            "time": "11:00",
        },
    )
    assert r["success"] is False
    assert r["error"]["field"] == "name"


def test_book_with_session_id_requires_prior_identify(db_conn):
    clear_booking_gate_for_tests()
    date = "2026-09-10"
    fetch = execute_tool(db_conn, TOOL_FETCH_SLOTS, {"date": date})
    offered = fetch["data"]["available_slots"]
    register_offered_slots("sess-a", date, offered)

    r = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "Alex Kim",
            "phone": "+15550003333",
            "date": date,
            "time": offered[0],
        },
        session_id="sess-a",
    )
    assert r["success"] is False
    assert "identify_user" in r["error"]["message"].lower()


def test_book_with_session_id_requires_time_in_last_fetch(db_conn):
    clear_booking_gate_for_tests()
    date = "2026-09-11"
    register_verified_phone("sess-b", "+15550004444")
    register_offered_slots("sess-b", date, ["09:00", "09:30"])

    r = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "Alex Kim",
            "phone": "+15550004444",
            "date": date,
            "time": "12:00",
        },
        session_id="sess-b",
    )
    assert r["success"] is False
    assert r["error"]["field"] == "time"


def test_book_full_session_gate_succeeds(db_conn):
    clear_booking_gate_for_tests()
    date = "2026-09-12"
    fetch = execute_tool(db_conn, TOOL_FETCH_SLOTS, {"date": date})
    offered = fetch["data"]["available_slots"]
    register_verified_phone("sess-c", "+15550005555")
    register_offered_slots("sess-c", date, offered)
    t = offered[min(3, len(offered) - 1)]

    r = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "Morgan Vale",
            "phone": "+15550005555",
            "date": date,
            "time": t,
        },
        session_id="sess-c",
    )
    assert r["success"] is True


def test_booking_gate_survives_session_handoff_to_phone(db_conn):
    """identify under client label + fetch/book under normalized phone share gate state."""
    clear_booking_gate_for_tests()
    date = "2026-09-20"
    fetch = execute_tool(db_conn, TOOL_FETCH_SLOTS, {"date": date})
    offered = fetch["data"]["available_slots"]
    register_verified_phone("display-name", "+15550007777")
    register_offered_slots("+15550007777", date, offered)
    t = offered[0]
    r = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "Taylor Smith",
            "phone": "+15550007777",
            "date": date,
            "time": t,
        },
        session_id="+15550007777",
    )
    assert r["success"] is True


def test_booking_gate_mirrors_fetch_from_client_label_to_phone(db_conn):
    clear_booking_gate_for_tests()
    date = "2026-09-21"
    register_verified_phone("anon-ui", "+15550008888")
    fetch = execute_tool(db_conn, TOOL_FETCH_SLOTS, {"date": date})
    offered = fetch["data"]["available_slots"]
    register_offered_slots("anon-ui", date, offered)
    t = offered[1] if len(offered) > 1 else offered[0]
    r = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "Jordan Lee",
            "phone": "+15550008888",
            "date": date,
            "time": t,
        },
        session_id="+15550008888",
    )
    assert r["success"] is True


def test_modify_rejects_non_template_time(db_conn):
    date = "2031-02-01"
    fetch = execute_tool(db_conn, TOOL_FETCH_SLOTS, {"date": date})
    t0 = fetch["data"]["available_slots"][0]
    book = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {
            "name": "Riley",
            "phone": "+15550006666",
            "date": date,
            "time": t0,
        },
    )
    appt_id = book["data"]["appointment"]["id"]

    mod = execute_tool(
        db_conn,
        TOOL_MODIFY_APPOINTMENT,
        {
            "appointment_id": appt_id,
            "phone": "+15550006666",
            "new_date": date,
            "new_time": "18:30",
        },
    )
    assert mod["success"] is False
    assert mod["error"]["code"] == "validation_error"
