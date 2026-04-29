from app.agent.finalize_guard import apply_tool_truth_guard
from app.tools.executor import TOOL_BOOK_APPOINTMENT, TOOL_CANCEL_APPOINTMENT


def test_guard_replaces_llm_when_book_failed():
    te = {
        "success": False,
        "tool": "book_appointment",
        "error": {"code": "double_booking", "message": "Slot taken"},
    }
    out = apply_tool_truth_guard(
        TOOL_BOOK_APPOINTMENT,
        te,
        "Great, you are all booked for Tuesday!",
    )
    assert "no longer available" in out.lower()
    assert "tuesday" not in out.lower()


def test_guard_keeps_llm_when_book_succeeded():
    te = {"success": True, "tool": "book_appointment", "data": {}}
    spoken = "You are confirmed for April 30 at four PM."
    assert apply_tool_truth_guard(TOOL_BOOK_APPOINTMENT, te, spoken) == spoken


def test_guard_failed_cancel():
    te = {"success": False, "tool": "cancel_appointment", "error": {"code": "not_found", "message": "nope"}}
    out = apply_tool_truth_guard(TOOL_CANCEL_APPOINTMENT, te, "Cancelled.")
    assert "nope" in out
