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
            if "require" in msg.lower() or "fetch_slots" in msg.lower() or "identify_user" in msg:
                return (
                    "I need to confirm your details in the right order before I can reserve that slot—"
                    "could you tell me again which day and time you'd like, after we've confirmed your phone number?"
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
