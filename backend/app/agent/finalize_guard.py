"""Override spoken replies when the LLM drifts from tool outcomes (booking/cancel/modify truth)."""

from __future__ import annotations

from typing import Any

from app.tools.executor import (
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_MODIFY_APPOINTMENT,
)


def _err(tool_execution: dict[str, Any] | None) -> tuple[str | None, str]:
    if not isinstance(tool_execution, dict):
        return None, ""
    e = tool_execution.get("error")
    if not isinstance(e, dict):
        return None, ""
    code = e.get("code")
    msg = str(e.get("message") or "").strip()
    c = str(code).strip() if code is not None else None
    return c, msg


def _booking_prerequisite_order_issue(msg: str) -> bool:
    """True only when the failure is identify_user / fetch_slots ordering — not time/grid wording."""
    m = msg.strip()
    low = m.lower()
    if low.startswith("bookings require identify_user"):
        return True
    if low.startswith("bookings require fetch_slots for this date first"):
        return True
    return False


def _time_or_grid_booking_issue(msg: str) -> bool:
    """Slot/time problems that should be explained plainly (not the phone-order script)."""
    low = msg.lower()
    return (
        "not in the available slots" in low
        or "not a clinic slot" in low
        or ("time " in low and "clinic" in low)
        or ("pick a listed time" in low and "bookings require" not in low)
    )


def apply_tool_truth_guard(
    plan_tool: str,
    tool_execution: dict[str, Any] | None,
    llm_final: str,
) -> str:
    """When a mutating tool fails, never return the LLM's possibly wrong success wording."""
    if plan_tool not in (
        TOOL_BOOK_APPOINTMENT,
        TOOL_CANCEL_APPOINTMENT,
        TOOL_MODIFY_APPOINTMENT,
    ):
        return llm_final.strip()

    if isinstance(tool_execution, dict) and tool_execution.get("success") is True:
        return llm_final.strip()

    code, msg = _err(tool_execution)

    if plan_tool == TOOL_BOOK_APPOINTMENT:
        if code == "double_booking":
            return (
                "That time slot is no longer available—it may have just been taken. "
                "Let's pick another time that still works for you; I can read out what's open."
            )
        if code == "conflict":
            return (
                "That time conflicts with an existing booking. "
                "Please choose a different slot from the times I listed for that day."
            )
        if code == "validation_error" and msg:
            if _time_or_grid_booking_issue(msg):
                return (
                    "That time is not on our list of bookable slots for this clinic—"
                    "for example we may only offer every half hour up to the last morning or afternoon slot shown. "
                    "I can list the exact times that are open for your day, or if you already have a booking and only "
                    "need to fix the time, say so and we'll adjust that appointment instead of making a second one."
                )
            if _booking_prerequisite_order_issue(msg):
                return (
                    "I need to confirm your details in the right order before I can reserve that slot—"
                    "first your phone number on file, then I'll read open times for your date, then we lock the time. "
                    "If you already gave those and only want to correct a time, ask me to look up your appointments and change that booking."
                )
            return msg
        return (
            "I wasn't able to complete the booking just now. "
            "Let's double-check the date, time, and your phone number, and try again."
        )

    if plan_tool == TOOL_CANCEL_APPOINTMENT:
        if code == "not_found" or code == "validation_error":
            return msg or "I couldn't find that appointment to cancel. If you have another detail, share it and we'll try again."
        return "I wasn't able to cancel that appointment. Please verify the booking and your phone number."

    if plan_tool == TOOL_MODIFY_APPOINTMENT:
        if code in ("not_found", "conflict", "validation_error", "double_booking") and msg:
            return msg
        return "I wasn't able to reschedule that appointment. Let's confirm the new date and time from the open slots."

    return llm_final.strip()
