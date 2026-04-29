"""
Repeatable DB + tool matrix (no LLM): booking, double-book, modify, cancel, retrieve.
For full-stack runs, use ``e2e_integration_real.sh`` or set ``RUN_HTTP=1`` with ``qa_scenario_matrix.sh``.
"""

from __future__ import annotations

from app.tools.executor import (
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_FETCH_SLOTS,
    TOOL_MODIFY_APPOINTMENT,
    TOOL_RETRIEVE_APPOINTMENTS,
    execute_tool,
)


def test_matrix_book_modify_cancel_retrieve(db_conn):
    d = "2030-01-15"
    fetch = execute_tool(db_conn, TOOL_FETCH_SLOTS, {"date": d})
    assert fetch["success"] is True
    times = fetch["data"]["available_slots"]
    assert isinstance(times, list) and len(times) > 0
    t0, t1 = times[0], times[1]

    book = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {"name": "QA User", "phone": "+15550001111", "date": d, "time": t0},
    )
    assert book["success"] is True
    appt_id = book["data"]["appointment"]["id"]

    dup = execute_tool(
        db_conn,
        TOOL_BOOK_APPOINTMENT,
        {"name": "Other", "phone": "+15550002222", "date": d, "time": t0},
    )
    assert dup["success"] is False
    assert dup["error"]["code"] == "double_booking"

    mod = execute_tool(
        db_conn,
        TOOL_MODIFY_APPOINTMENT,
        {
            "appointment_id": appt_id,
            "phone": "+15550001111",
            "new_date": d,
            "new_time": t1,
        },
    )
    assert mod["success"] is True
    assert mod["data"]["appointment"]["time"] == t1

    can = execute_tool(
        db_conn,
        TOOL_CANCEL_APPOINTMENT,
        {"appointment_id": appt_id, "phone": "+15550001111"},
    )
    assert can["success"] is True
    assert can["data"]["appointment"]["status"] == "cancelled"

    lst = execute_tool(
        db_conn,
        TOOL_RETRIEVE_APPOINTMENTS,
        {"phone": "+15550001111", "include_cancelled": True},
    )
    assert lst["success"] is True
    assert len(lst["data"]["appointments"]) >= 1
