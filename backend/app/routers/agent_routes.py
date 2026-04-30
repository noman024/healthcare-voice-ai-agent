from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["agent"])


class AgentTurnBody(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field(default="default", max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)


@router.post("/agent/turn")
def agent_turn(body: AgentTurnBody, request: Request) -> dict[str, Any]:
    """Planner LLM → tool execution (if any) → finalizer LLM. Requires a running Ollama server."""
    from app.agent.runner import run_turn

    try:
        return run_turn(
            request.app.state.db_conn,
            user_message=body.message.strip(),
            session_id=(body.session_id.strip() or "default"),
            persistence_session_id=(
                body.conversation_id.strip()
                if isinstance(body.conversation_id, str) and body.conversation_id.strip()
                else None
            ),
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM service error: {e}") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class AgentSummaryBody(BaseModel):
    """Summarize transcript for ``session_id`` (hydrates from SQLite when ``CONVERSATION_PERSIST`` is on)."""

    session_id: str = Field(default="default", max_length=128)
    conversation_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional stable id for transcript storage; when set, summary loads history under this key.",
    )
    phone: str | None = Field(
        default=None,
        max_length=32,
        description="Optional E.164-style phone to list DB appointments; else session_id is tried if it looks like a phone.",
    )
    transcript_fallback: str | None = Field(
        default=None,
        max_length=200_000,
        description="When SQLite has no rows (e.g. LiveKit mirror not configured), use this dialogue text for summarization.",
    )


@router.post("/agent/summary")
def agent_summary(body: AgentSummaryBody, request: Request) -> dict[str, Any]:
    """LLM summary + appointment snapshot + server timestamp (same session memory as conversation routes)."""
    from app.agent.summary import build_agent_summary

    sid = (body.session_id.strip() or "default")
    tid = (body.conversation_id.strip() if isinstance(body.conversation_id, str) and body.conversation_id.strip() else None)
    cost = os.getenv("INCLUDE_COST_HINTS", "0").strip().lower() in ("1", "true", "yes", "on")
    try:
        return build_agent_summary(
            request.app.state.db_conn,
            session_id=sid,
            conversation_id=tid,
            phone=(body.phone.strip() if isinstance(body.phone, str) and body.phone.strip() else None),
            transcript_fallback=(
                body.transcript_fallback.strip()
                if isinstance(body.transcript_fallback, str) and body.transcript_fallback.strip()
                else None
            ),
            include_cost_hints=cost,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM service error: {e}") from e
