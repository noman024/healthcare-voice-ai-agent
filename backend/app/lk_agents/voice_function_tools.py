"""LiveKit ``function_tool`` bindings that delegate to :mod:`app.tools.executor`."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

from livekit.agents import function_tool
from livekit.agents.voice.events import RunContext

from app.llm.prompts import build_plan_system
from app.session_booking_gate import register_offered_slots, register_verified_phone
from app.tools.executor import (
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_END_CONVERSATION,
    TOOL_FETCH_SLOTS,
    TOOL_IDENTIFY_USER,
    TOOL_MODIFY_APPOINTMENT,
    TOOL_RETRIEVE_APPOINTMENTS,
    execute_tool,
)

from .userdata import HealthcareUserdata
from .worker_publish import publish_ui_payload


def _tool_result_payload(out: dict[str, Any]) -> str:
    return json.dumps(out, default=str)


async def _voice_execute_tool(
    u: HealthcareUserdata,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    session_id: str | None,
) -> dict[str, Any]:
    """Publish in-flight tool status to the browser, then run SQLite tools off the event loop."""
    if u.room and u.ui_dest_identity:
        await publish_ui_payload(
            u.room,
            u.ui_dest_identity,
            {"kind": "tool", "tool_execution": {"phase": "running", "tool": tool_name}},
        )
    return await asyncio.to_thread(
        execute_tool,
        u.conn,
        tool_name,
        arguments,
        session_id=session_id,
    )


def voice_system_instructions() -> str:
    planner = build_plan_system(today_iso=date.today().isoformat())
    return (
        planner
        + "\n\n---\nYou are speaking with a patient over voice. "
        "Call tools to act; keep spoken replies short and natural. "
        "After a tool result, summarize in plain language (no internal tool names).\n"
    )


@function_tool
async def lk_identify_user(ctx: RunContext[HealthcareUserdata], phone: str, name: str | None = None) -> str:
    u = ctx.userdata
    prior_sid = u.session_key
    out = await _voice_execute_tool(
        u,
        TOOL_IDENTIFY_USER,
        {"phone": phone, **({"name": name} if name else {})},
        session_id=None,
    )
    if out.get("success"):
        data = out.get("data") or {}
        ph = data.get("phone")
        if isinstance(ph, str) and ph.strip():
            ph = ph.strip()
            register_verified_phone(prior_sid, ph)
            register_verified_phone(ph, ph)
            u.session_key = ph
    return _tool_result_payload(out)


@function_tool
async def lk_fetch_slots(ctx: RunContext[HealthcareUserdata], date: str) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(u, TOOL_FETCH_SLOTS, {"date": date}, session_id=u.session_key)
    if out.get("success") and isinstance(out.get("data"), dict):
        d = out["data"]
        register_offered_slots(u.session_key, str(d.get("date") or ""), d.get("available_slots") or [])
    return _tool_result_payload(out)


@function_tool
async def lk_book_appointment(
    ctx: RunContext[HealthcareUserdata],
    name: str,
    phone: str,
    date: str,
    time: str,
) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(
        u,
        TOOL_BOOK_APPOINTMENT,
        {"name": name, "phone": phone, "date": date, "time": time},
        session_id=u.session_key,
    )
    return _tool_result_payload(out)


@function_tool
async def lk_retrieve_appointments(
    ctx: RunContext[HealthcareUserdata],
    phone: str,
    include_cancelled: bool = False,
) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(
        u,
        TOOL_RETRIEVE_APPOINTMENTS,
        {"phone": phone, "include_cancelled": include_cancelled},
        session_id=u.session_key,
    )
    return _tool_result_payload(out)


@function_tool
async def lk_cancel_appointment(
    ctx: RunContext[HealthcareUserdata],
    appointment_id: int,
    phone: str,
) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(
        u,
        TOOL_CANCEL_APPOINTMENT,
        {"appointment_id": appointment_id, "phone": phone},
        session_id=u.session_key,
    )
    return _tool_result_payload(out)


@function_tool
async def lk_modify_appointment(
    ctx: RunContext[HealthcareUserdata],
    appointment_id: int,
    phone: str,
    new_date: str,
    new_time: str,
) -> str:
    u = ctx.userdata
    out = await _voice_execute_tool(
        u,
        TOOL_MODIFY_APPOINTMENT,
        {
            "appointment_id": appointment_id,
            "phone": phone,
            "new_date": new_date,
            "new_time": new_time,
        },
        session_id=u.session_key,
    )
    return _tool_result_payload(out)


@function_tool
async def lk_end_conversation(ctx: RunContext[HealthcareUserdata], reason: str | None = None) -> str:
    u = ctx.userdata
    args: dict[str, Any] = {}
    if reason:
        args["reason"] = reason
    out = await _voice_execute_tool(u, TOOL_END_CONVERSATION, args, session_id=u.session_key)
    return _tool_result_payload(out)


HEALTH_TOOLS = [
    lk_identify_user,
    lk_fetch_slots,
    lk_book_appointment,
    lk_retrieve_appointments,
    lk_cancel_appointment,
    lk_modify_appointment,
    lk_end_conversation,
]
