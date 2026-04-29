from __future__ import annotations

import logging
import sqlite3
from typing import Any

from app.session_booking_gate import assert_booking_gate_ok
from app.db import appointments as appt_repo
from app.tools import slots
from app.tools.validation import (
    ToolValidationError,
    assert_date_not_in_past,
    normalize_phone,
    optional_str,
    parse_date_str,
    parse_time_str,
    require_int,
    require_str,
    validate_booking_display_name,
    validate_clinic_template_time,
)

logger = logging.getLogger(__name__)

TOOL_IDENTIFY_USER = "identify_user"
TOOL_FETCH_SLOTS = "fetch_slots"
TOOL_BOOK_APPOINTMENT = "book_appointment"
TOOL_RETRIEVE_APPOINTMENTS = "retrieve_appointments"
TOOL_CANCEL_APPOINTMENT = "cancel_appointment"
TOOL_MODIFY_APPOINTMENT = "modify_appointment"
TOOL_END_CONVERSATION = "end_conversation"

TOOL_NAMES = frozenset(
    {
        TOOL_IDENTIFY_USER,
        TOOL_FETCH_SLOTS,
        TOOL_BOOK_APPOINTMENT,
        TOOL_RETRIEVE_APPOINTMENTS,
        TOOL_CANCEL_APPOINTMENT,
        TOOL_MODIFY_APPOINTMENT,
        TOOL_END_CONVERSATION,
    },
)


def _ok(tool: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "tool": tool, "data": data}


def _fail(
    tool: str,
    code: str,
    message: str,
    *,
    field: str | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if field:
        err["field"] = field
    return {"success": False, "tool": tool, "error": err}


def _tool_identify_user(arguments: dict[str, Any]) -> dict[str, Any]:
    phone = normalize_phone(require_str(arguments, "phone"))
    name = optional_str(arguments, "name")
    logger.info("identify_user phone=%s name=%s", phone, name)
    return {"identified": True, "phone": phone, "name": name}


def _tool_fetch_slots(conn: sqlite3.Connection, arguments: dict[str, Any]) -> dict[str, Any]:
    date = parse_date_str(require_str(arguments, "date"))
    assert_date_not_in_past(date)
    candidates = slots.day_slot_candidates()
    available = appt_repo.list_bookable_slot_times(conn, date, candidates)
    logger.info("fetch_slots date=%s available=%s", date, len(available))
    return {"date": date, "available_slots": available}


def _tool_book_appointment(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    name = validate_booking_display_name(require_str(arguments, "name"))
    phone = normalize_phone(require_str(arguments, "phone"))
    date = parse_date_str(require_str(arguments, "date"))
    assert_date_not_in_past(date)
    time = parse_time_str(require_str(arguments, "time"))
    validate_clinic_template_time(time)
    assert_booking_gate_ok(session_id, phone, date, time)
    appt = appt_repo.book_appointment(conn, name=name, phone=phone, date=date, time=time)
    logger.info("book_appointment id=%s %s %s", appt.id, date, time)
    return {
        "appointment": {
            "id": appt.id,
            "name": appt.name,
            "phone": appt.phone,
            "date": appt.date,
            "time": appt.time,
            "status": appt.status,
        },
    }


def _tool_retrieve_appointments(conn: sqlite3.Connection, arguments: dict[str, Any]) -> dict[str, Any]:
    phone = normalize_phone(require_str(arguments, "phone"))
    include_cancelled = bool(arguments.get("include_cancelled", False))
    rows = appt_repo.list_appointments_for_phone(conn, phone, include_cancelled=include_cancelled)
    logger.info("retrieve_appointments phone=%s count=%s", phone, len(rows))
    return {
        "phone": phone,
        "appointments": [
            {
                "id": r.id,
                "name": r.name,
                "phone": r.phone,
                "date": r.date,
                "time": r.time,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


def _tool_cancel_appointment(conn: sqlite3.Connection, arguments: dict[str, Any]) -> dict[str, Any]:
    appointment_id = require_int(arguments, "appointment_id")
    phone = normalize_phone(require_str(arguments, "phone"))
    appt = appt_repo.cancel_appointment(conn, appointment_id, phone=phone)
    logger.info("cancel_appointment id=%s", appointment_id)
    return {
        "appointment": {
            "id": appt.id,
            "name": appt.name,
            "phone": appt.phone,
            "date": appt.date,
            "time": appt.time,
            "status": appt.status,
        },
    }


def _tool_modify_appointment(conn: sqlite3.Connection, arguments: dict[str, Any]) -> dict[str, Any]:
    appointment_id = require_int(arguments, "appointment_id")
    phone = normalize_phone(require_str(arguments, "phone"))
    new_date = parse_date_str(require_str(arguments, "new_date"))
    assert_date_not_in_past(new_date)
    new_time = parse_time_str(require_str(arguments, "new_time"))
    validate_clinic_template_time(new_time)
    appt = appt_repo.modify_appointment_timeslot(
        conn,
        appointment_id,
        phone=phone,
        new_date=new_date,
        new_time=new_time,
    )
    logger.info("modify_appointment id=%s -> %s %s", appointment_id, new_date, new_time)
    return {
        "appointment": {
            "id": appt.id,
            "name": appt.name,
            "phone": appt.phone,
            "date": appt.date,
            "time": appt.time,
            "status": appt.status,
        },
    }


def _tool_end_conversation(_conn: sqlite3.Connection | None, arguments: dict[str, Any]) -> dict[str, Any]:
    reason = optional_str(arguments, "reason")
    logger.info("end_conversation reason=%s", reason)
    return {"ended": True, "reason": reason}


def execute_tool(
    conn: sqlite3.Connection,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Run a tool by name with JSON-like argument dict; never raises for domain errors.

    ``session_id`` enables stricter booking rules (identify + fetch_slots ordering).
    ``POST /tools/invoke`` omits it so integration tests stay direct.
    """
    args = dict(arguments or {})
    name = str(tool_name).strip()
    if name not in TOOL_NAMES:
        logger.warning("unknown_tool %s", tool_name)
        return _fail(name, "unknown_tool", f"Unknown tool '{tool_name}'.")

    try:
        if name == TOOL_IDENTIFY_USER:
            return _ok(name, _tool_identify_user(args))
        if name == TOOL_FETCH_SLOTS:
            return _ok(name, _tool_fetch_slots(conn, args))
        if name == TOOL_BOOK_APPOINTMENT:
            return _ok(name, _tool_book_appointment(conn, args, session_id=session_id))
        if name == TOOL_RETRIEVE_APPOINTMENTS:
            return _ok(name, _tool_retrieve_appointments(conn, args))
        if name == TOOL_CANCEL_APPOINTMENT:
            return _ok(name, _tool_cancel_appointment(conn, args))
        if name == TOOL_MODIFY_APPOINTMENT:
            return _ok(name, _tool_modify_appointment(conn, args))
        if name == TOOL_END_CONVERSATION:
            return _ok(name, _tool_end_conversation(conn, args))
    except ToolValidationError as e:
        logger.info("validation_error tool=%s %s", name, e)
        return _fail(name, "validation_error", str(e), field=e.field)
    except appt_repo.DoubleBookingError as e:
        logger.info("double_booking tool=%s %s", name, e)
        return _fail(name, "double_booking", str(e))
    except appt_repo.AppointmentNotFoundError as e:
        logger.info("not_found tool=%s %s", name, e)
        return _fail(name, "not_found", str(e))
    except appt_repo.AppointmentConflictError as e:
        logger.info("conflict tool=%s %s", name, e)
        return _fail(name, "conflict", str(e))

    return _fail(name, "internal_error", "Tool branch not implemented.")
