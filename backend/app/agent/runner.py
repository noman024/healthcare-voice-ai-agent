"""Turn runner: planner LLM → optional tool execution → finalizer LLM."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx

from app.agent.memory import get_session_memory
from app.agent.plan_coerce import coerce_agent_plan
from app.agent.plan_precheck import apply_plan_precheck
from app.session_booking_gate import register_offered_slots, register_verified_phone
from app.db.conversation_messages import hydrate_session_memory, persist_exchange
from app.llm import ollama as ollama_client
from app.llm.parser import parse_plan_with_retry
from app.llm.prompts import FINALIZE_SYSTEM, build_plan_system
from app.llm.schema import AgentPlan
from app.tools.executor import TOOL_FETCH_SLOTS, TOOL_IDENTIFY_USER, execute_tool

logger = logging.getLogger(__name__)


def iter_turn_events(
    conn: sqlite3.Connection,
    *,
    user_message: str,
    session_id: str = "default",
    persistence_session_id: str | None = None,
    client: httpx.Client | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Same pipeline as run_turn, yielding structured events for WebSocket / activity feeds.

    Order: plan → optional tool → done (includes final_response and full plan payload).

    ``persistence_session_id`` (if set) keys in-memory + SQLite transcript storage while
    ``session_id`` still drives tools (e.g. UI handoff to normalized phone for booking gate).
    """
    pid = (persistence_session_id or "").strip() or session_id
    mem = get_session_memory(pid)
    hydrate_session_memory(mem, conn, pid)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_plan_system(today_iso=date.today().isoformat())},
    ]
    messages.extend(mem.as_ollama_messages())
    messages.append({"role": "user", "content": user_message})

    planner_model = (os.getenv("OLLAMA_PLANNER_MODEL") or "").strip() or None

    def _complete(m: list[dict[str, Any]]) -> str:
        return ollama_client.ollama_chat(
            m,
            client=client,
            response_format="json",
            model=planner_model,
        )

    try:
        plan = parse_plan_with_retry(_complete, messages, max_attempts=3)
    except ValueError:
        logger.warning(
            "agent_planner_exhausted session=%s; using fallback plan (no tool)",
            session_id,
        )
        plan = AgentPlan(
            intent="planner_exhausted",
            tool="none",
            arguments={},
            response="I'm having trouble with that step—please say briefly what you need.",
        )

    planner_fallback = plan.intent == "planner_exhausted"

    plan, coerced_plan = coerce_agent_plan(plan, user_message=user_message)
    if coerced_plan:
        logger.info("agent_plan_coerced session=%s tool=%s intent=%s", session_id, plan.tool, plan.intent)

    plan = apply_plan_precheck(plan)

    plan_dict = plan.model_dump()
    yield {"type": "plan", "session_id": session_id, "plan": plan_dict}

    tool_execution: dict[str, Any] | None = None
    session_identity: dict[str, str] | None = None
    if plan.tool != "none":
        tool_execution = execute_tool(conn, plan.tool, plan.arguments, session_id=session_id)
        logger.info(
            "agent_tool_ran session=%s tool=%s success=%s",
            session_id,
            plan.tool,
            tool_execution.get("success") if isinstance(tool_execution, dict) else None,
        )
        yield {"type": "tool", "session_id": session_id, "tool_execution": tool_execution}
        if isinstance(tool_execution, dict) and tool_execution.get("success") is True:
            data = tool_execution.get("data") or {}
            if plan.tool == TOOL_FETCH_SLOTS:
                register_offered_slots(session_id, str(data.get("date") or ""), data.get("available_slots") or [])
            if plan.tool == TOOL_IDENTIFY_USER and data.get("phone"):
                ph = str(data["phone"]).strip()
                register_verified_phone(session_id, ph)
                if ph:
                    session_identity = {"suggested_session_id": ph}

    finalize_payload = {
        "user_message": user_message,
        "plan": plan_dict,
        "tool_execution": tool_execution,
        "planner_fallback": planner_fallback,
    }
    finalize_messages: list[dict[str, Any]] = [
        {"role": "system", "content": FINALIZE_SYSTEM},
        {"role": "user", "content": json.dumps(finalize_payload, default=str)},
    ]
    finalize_model = (os.getenv("OLLAMA_FINALIZE_MODEL") or "").strip() or None
    final_response = ollama_client.ollama_chat(
        finalize_messages,
        client=client,
        model=finalize_model,
    ).strip()

    mem.append_exchange(user_message, final_response)
    persist_exchange(
        conn,
        session_id=pid,
        user_message=user_message,
        assistant_message=final_response,
    )

    fallback_warning = (
        "The planner temporarily could not produce structured instructions; clarify or repeat."
        if planner_fallback
        else None
    )

    yield {
        "type": "done",
        "session_id": session_id,
        "final_response": final_response,
        "plan": plan_dict,
        "tool_execution": tool_execution,
        "session_identity": session_identity,
        "warning": fallback_warning,
    }


def run_turn(
    conn: sqlite3.Connection,
    *,
    user_message: str,
    session_id: str = "default",
    persistence_session_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """
    One user message → structured plan → tool (unless tool is "none") → final natural reply.
    Appends this exchange to in-memory session history (last 10 turns / 20 messages).
    """
    last: dict[str, Any] | None = None
    for ev in iter_turn_events(
        conn,
        user_message=user_message,
        session_id=session_id,
        persistence_session_id=persistence_session_id,
        client=client,
    ):
        if ev.get("type") == "done":
            last = ev
    if not last:
        raise RuntimeError("Agent turn produced no terminal event.")
    out = {k: v for k, v in last.items() if k != "type"}
    return out
