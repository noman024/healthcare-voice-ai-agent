"""Normalize risky planner output before tool execution — fewer validation-only failures."""

from __future__ import annotations

import logging
from typing import Any

from datetime import datetime

from app.llm.schema import AgentPlan
from app.tools.executor import (
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_END_CONVERSATION,
    TOOL_FETCH_SLOTS,
    TOOL_IDENTIFY_USER,
    TOOL_MODIFY_APPOINTMENT,
    TOOL_RETRIEVE_APPOINTMENTS,
)
from app.tools.validation import (
    ToolValidationError,
    _DATE_RE,
    _TIME_RE,
    calendar_today,
    normalize_phone,
    person_name_precheck_ok,
)

logger = logging.getLogger(__name__)


def _args(plan: AgentPlan) -> dict[str, Any]:
    return dict(plan.arguments or {})


def _demote(plan: AgentPlan, *, draft: str, note: str) -> AgentPlan:
    logger.info(
        "plan_precheck_demoted original_tool=%s reason=%s",
        plan.tool,
        note,
    )
    return plan.model_copy(
        update={
            "tool": "none",
            "arguments": {},
            "response": draft,
            "intent": plan.intent if str(plan.intent).strip() else "clarification",
        }
    )


def _phone_arg_ok(raw: Any) -> bool:
    if raw is None or not str(raw).strip():
        return False
    try:
        normalize_phone(str(raw))
        return True
    except ToolValidationError:
        return False


def _appointment_id_ok(raw: Any) -> bool:
    if raw is None:
        return False
    try:
        int(raw)
        return True
    except (TypeError, ValueError):
        return False


def apply_plan_precheck(plan: AgentPlan) -> AgentPlan:
    """
    If the model picked a tool but omitted or invalidates required arguments, demote to
    ``tool: none`` with a clear draft ``response`` so the finalizer can speak helpfully
    without a validation_error round-trip.
    """
    if plan.tool == "none" or plan.tool == TOOL_END_CONVERSATION:
        return plan

    a = _args(plan)
    t = plan.tool

    if t == TOOL_IDENTIFY_USER:
        if not _phone_arg_ok(a.get("phone")):
            return _demote(
                plan,
                draft=(
                    "Could you share the phone number you want on file, with country code "
                    "(for example +1 or +44)?"
                ),
                note="identify_user_missing_or_invalid_phone",
            )
        return plan

    if t == TOOL_FETCH_SLOTS:
        d = a.get("date")
        ds = str(d).strip() if d is not None else ""
        if not ds or not _DATE_RE.match(ds):
            return _demote(
                plan,
                draft=(
                    "Which day should I check? Please give a single calendar date as "
                    "YYYY-MM-DD, for example 2026-06-24."
                ),
                note="fetch_slots_missing_or_invalid_date",
            )
        try:
            if datetime.strptime(ds, "%Y-%m-%d").date() < calendar_today():
                t_iso = calendar_today().isoformat()
                return _demote(
                    plan,
                    draft=(
                        f"I can't show openings for {ds}—that day is already before today "
                        f"({t_iso}). Which day from today onward should I check?"
                    ),
                    note="fetch_slots_past_date",
                )
        except ValueError:
            return _demote(
                plan,
                draft=(
                    "Which day should I check? Please give a valid calendar date as "
                    "YYYY-MM-DD."
                ),
                note="fetch_slots_invalid_calendar_date",
            )
        return plan

    if t == TOOL_BOOK_APPOINTMENT:
        missing: list[str] = []
        if not str(a.get("name") or "").strip():
            missing.append("name")
        elif not person_name_precheck_ok(a.get("name")):
            return _demote(
                plan,
                draft="Could you share your full name for the appointment (not a placeholder)?",
                note="book_appointment_placeholder_name",
            )
        if not _phone_arg_ok(a.get("phone")):
            missing.append("phone")
        d = a.get("date")
        ds_book = str(d).strip() if d is not None else ""
        if not ds_book or not _DATE_RE.match(ds_book):
            missing.append("date (YYYY-MM-DD)")
        else:
            try:
                if datetime.strptime(ds_book, "%Y-%m-%d").date() < calendar_today():
                    t_iso = calendar_today().isoformat()
                    return _demote(
                        plan,
                        draft=(
                            f"I can't book for {ds_book}—that's before today ({t_iso}). "
                            "Pick today or a future day."
                        ),
                        note="book_appointment_past_date",
                    )
            except ValueError:
                missing.append("date (YYYY-MM-DD)")
        tm = a.get("time")
        if not tm or not _TIME_RE.match(str(tm).strip()):
            missing.append("time (HH:MM 24-hour)")
        if missing:
            return _demote(
                plan,
                draft=(
                    "To book, I still need: "
                    + ", ".join(missing)
                    + ". Could you provide those?"
                ),
                note="book_appointment_incomplete",
            )
        return plan

    if t == TOOL_RETRIEVE_APPOINTMENTS:
        if not _phone_arg_ok(a.get("phone")):
            return _demote(
                plan,
                draft="What phone number should I look up, with country code?",
                note="retrieve_missing_phone",
            )
        return plan

    if t == TOOL_CANCEL_APPOINTMENT:
        if not _appointment_id_ok(a.get("appointment_id")) or not _phone_arg_ok(a.get("phone")):
            return _demote(
                plan,
                draft=(
                    "To cancel, I need the appointment ID and the phone number on that booking. "
                    "If you are not sure of the ID, say “list my appointments” and your phone number."
                ),
                note="cancel_missing_id_or_phone",
            )
        return plan

    if t == TOOL_MODIFY_APPOINTMENT:
        missing_m: list[str] = []
        if not _appointment_id_ok(a.get("appointment_id")):
            missing_m.append("appointment_id")
        if not _phone_arg_ok(a.get("phone")):
            missing_m.append("phone")
        nd = a.get("new_date")
        nds = str(nd).strip() if nd is not None else ""
        if not nds or not _DATE_RE.match(nds):
            missing_m.append("new_date (YYYY-MM-DD)")
        else:
            try:
                if datetime.strptime(nds, "%Y-%m-%d").date() < calendar_today():
                    t_iso = calendar_today().isoformat()
                    return _demote(
                        plan,
                        draft=(
                            f"I can't move the appointment to {nds}—that date is before today "
                            f"({t_iso}). Choose today or a future day."
                        ),
                        note="modify_past_new_date",
                    )
            except ValueError:
                missing_m.append("new_date (YYYY-MM-DD)")
        nt = a.get("new_time")
        if not nt or not _TIME_RE.match(str(nt).strip()):
            missing_m.append("new_time (HH:MM)")
        if missing_m:
            return _demote(
                plan,
                draft=(
                    "To reschedule I need your appointment ID, phone on file, and the "
                    "new date and time (date as YYYY-MM-DD, time as HH:MM). "
                    f"Still missing: {', '.join(missing_m)}."
                ),
                note="modify_incomplete",
            )
        return plan

    return plan
