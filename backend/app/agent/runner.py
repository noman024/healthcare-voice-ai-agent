"""Turn runner: planner LLM → optional tool execution → finalizer LLM."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from typing import Any

import httpx

from app.agent.memory import get_session_memory
from app.agent.plan_coerce import coerce_agent_plan
from app.agent.plan_precheck import apply_plan_precheck
from app.db.conversation_messages import hydrate_session_memory, persist_exchange
from app.llm import ollama as ollama_client
from app.llm.parser import parse_plan_with_retry
from app.llm.prompts import FINALIZE_SYSTEM, PLAN_SYSTEM
from app.llm.schema import AgentPlan
from app.tools.executor import TOOL_IDENTIFY_USER, execute_tool

logger = logging.getLogger(__name__)


def iter_turn_events(
    conn: sqlite3.Connection,
    *,
    user_message: str,
    session_id: str = "default",
    client: httpx.Client | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Same pipeline as run_turn, yielding structured events for WebSocket / activity feeds.

    Order: plan → optional tool → done (includes final_response and full plan payload).
    """
    mem = get_session_memory(session_id)
    hydrate_session_memory(mem, conn, session_id)
    messages: list[dict[str, Any]] = [{"role": "system", "content": PLAN_SYSTEM}]
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
        tool_execution = execute_tool(conn, plan.tool, plan.arguments)
        logger.info(
            "agent_tool_ran session=%s tool=%s success=%s",
            session_id,
            plan.tool,
            tool_execution.get("success") if isinstance(tool_execution, dict) else None,
        )
        yield {"type": "tool", "session_id": session_id, "tool_execution": tool_execution}
        if (
            plan.tool == TOOL_IDENTIFY_USER
            and isinstance(tool_execution, dict)
            and tool_execution.get("success") is True
        ):
            data = tool_execution.get("data") or {}
            phone = data.get("phone")
            if phone is not None and str(phone).strip():
                session_identity = {"suggested_session_id": str(phone).strip()}

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
        session_id=session_id,
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
        client=client,
    ):
        if ev.get("type") == "done":
            last = ev
    if not last:
        raise RuntimeError("Agent turn produced no terminal event.")
    out = {k: v for k, v in last.items() if k != "type"}
    return out
