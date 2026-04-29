"""
Healthcare voice agent entrypoint for LiveKit Agents.

FastAPI remains the source for HTTP, `/livekit/token`, `/tools/invoke`, `/agent/summary`, and
optional non-LiveKit transports. This worker shares the same SQLite file for tool execution.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentSession, JobContext, WorkerOptions, cli, function_tool, stt
from livekit.agents.job import AutoSubscribe
from livekit.agents.voice import Agent
from livekit.agents.voice.events import RunContext
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero

from app.hardware.cuda_ld_path import prepend_cuda_ld_library_path
from app.llm.ollama import ollama_base_url
from app.llm.prompts import FINALIZE_SYSTEM, build_plan_system
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

from .stt_faster_whisper import FasterWhisperBatchSTT
from .tts_fastapi import FastApiPiperTTS
from .userdata import HealthcareUserdata

logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parent.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

load_dotenv(_BACKEND / ".env")
load_dotenv()


def _tool_result_payload(out: dict[str, Any]) -> str:
    return json.dumps(out, default=str)


def _voice_system_instructions() -> str:
    from datetime import date

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
    out = execute_tool(
        u.conn,
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
    out = execute_tool(u.conn, TOOL_FETCH_SLOTS, {"date": date}, session_id=u.session_key)
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
    out = execute_tool(
        u.conn,
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
    out = execute_tool(
        u.conn,
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
    out = execute_tool(
        u.conn,
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
    out = execute_tool(
        u.conn,
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
    out = execute_tool(u.conn, TOOL_END_CONVERSATION, args, session_id=u.session_key)
    return _tool_result_payload(out)


_HEALTH_TOOLS = [
    lk_identify_user,
    lk_fetch_slots,
    lk_book_appointment,
    lk_retrieve_appointments,
    lk_cancel_appointment,
    lk_modify_appointment,
    lk_end_conversation,
]


async def entrypoint(ctx: JobContext) -> None:
    prepend_cuda_ld_library_path()

    from app.db.database import connect, init_db

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    participant = await ctx.wait_for_participant()
    identity = str(getattr(participant, "identity", None) or "voice-user")

    conn = connect()
    init_db(conn)

    api_base = os.getenv("VOICE_API_BASE", "http://127.0.0.1:8000").strip().rstrip("/")
    ollama_url = f"{ollama_base_url().rstrip('/')}/v1"
    model = (os.getenv("OLLAMA_MODEL") or "qwen2.5:7b-instruct").strip()

    userdata = HealthcareUserdata(conn=conn, session_key=identity)
    vad = silero.VAD.load()
    batch_stt = FasterWhisperBatchSTT()
    pipeline_stt = stt.StreamAdapter(stt=batch_stt, vad=vad)

    llm_engine = lk_openai.LLM(
        model=model,
        base_url=ollama_url,
        api_key="ollama",
    )
    tts_engine = FastApiPiperTTS(base_url=api_base)

    instructions = _voice_system_instructions() + "\n---\nResponse tone:\n" + FINALIZE_SYSTEM
    agent = Agent(instructions=instructions, tools=_HEALTH_TOOLS)

    session = AgentSession(
        stt=pipeline_stt,
        llm=llm_engine,
        tts=tts_engine,
        vad=vad,
        userdata=userdata,
    )

    try:
        await session.start(agent=agent, room=ctx.room)
    finally:
        conn.close()


def run_worker() -> None:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        ),
    )


if __name__ == "__main__":
    run_worker()
