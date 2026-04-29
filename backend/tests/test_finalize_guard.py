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


def test_guard_time_not_clinic_slot_not_phone_order_script():
    te = {
        "success": False,
        "tool": "book_appointment",
        "error": {
            "code": "validation_error",
            "message": "Time 17:30 is not a clinic slot. Bookable grid is 16 half-hour steps (09:00–16:30). Use fetch_slots and pick a listed time.",
        },
    }
    out = apply_tool_truth_guard(TOOL_BOOK_APPOINTMENT, te, "You are booked.")
    assert "not on our list" in out.lower() or "bookable" in out.lower()
    assert "after we've confirmed" not in out.lower()
    assert "fetch_slots" not in out.lower()


def test_guard_time_not_in_offered_slots_plain_language():
    te = {
        "success": False,
        "tool": "book_appointment",
        "error": {
            "code": "validation_error",
            "message": "Time 11:30 was not in the available slots for 2026-05-05. Call fetch_slots again or pick a listed time.",
        },
    }
    out = apply_tool_truth_guard(TOOL_BOOK_APPOINTMENT, te, "OK.")
    assert "adjust that appointment" in out.lower() or "bookable" in out.lower()


def test_guard_bookings_require_identify_keeps_order_hint():
    te = {
        "success": False,
        "tool": "book_appointment",
        "error": {
            "code": "validation_error",
            "message": "Bookings require identify_user to succeed first with the same phone number.",
        },
    }
    out = apply_tool_truth_guard(TOOL_BOOK_APPOINTMENT, te, "OK.")
    assert "phone" in out.lower()
    assert "change that booking" in out.lower()


def test_guard_keeps_llm_when_book_succeeded():
    te = {"success": True, "tool": "book_appointment", "data": {}}
    spoken = "You are confirmed for April 30 at four PM."
    assert apply_tool_truth_guard(TOOL_BOOK_APPOINTMENT, te, spoken) == spoken


def test_guard_failed_cancel():
    te = {"success": False, "tool": "cancel_appointment", "error": {"code": "not_found", "message": "nope"}}
    out = apply_tool_truth_guard(TOOL_CANCEL_APPOINTMENT, te, "Cancelled.")
    assert "nope" in out
