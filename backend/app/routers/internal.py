from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.tools.executor import execute_tool

router = APIRouter(tags=["internal"])


def _db_inspect_enabled() -> bool:
    return os.getenv("ENABLE_DB_INSPECT", "0").strip().lower() in ("1", "true", "yes", "on")


@router.get("/internal/db/snapshot")
def internal_db_snapshot(
    request: Request,
    appointments_limit: int = 50,
    messages_limit: int = 50,
    session_id: str | None = None,
) -> dict[str, Any]:
    """
    Read-only JSON view of SQLite (appointments + conversation_messages).
    **Off by default** — set ``ENABLE_DB_INSPECT=1`` in ``backend/.env`` for local use only.
    Returns **404** when disabled so the route is not advertised in production.
    """
    if not _db_inspect_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    ap_lim = max(1, min(int(appointments_limit), 200))
    msg_lim = max(1, min(int(messages_limit), 200))
    conn = request.app.state.db_conn

    ap_total = int(conn.execute("SELECT COUNT(*) AS c FROM appointments").fetchone()["c"])
    msg_total = int(conn.execute("SELECT COUNT(*) AS c FROM conversation_messages").fetchone()["c"])

    ap_rows = conn.execute(
        f"SELECT * FROM appointments ORDER BY id DESC LIMIT {ap_lim}",
    ).fetchall()

    if session_id and session_id.strip():
        sid = session_id.strip()
        msg_rows = conn.execute(
            f"SELECT * FROM conversation_messages WHERE session_id = ? ORDER BY id DESC LIMIT {msg_lim}",
            (sid,),
        ).fetchall()
    else:
        msg_rows = conn.execute(
            f"SELECT * FROM conversation_messages ORDER BY id DESC LIMIT {msg_lim}",
        ).fetchall()

    return {
        "counts": {"appointments": ap_total, "conversation_messages": msg_total},
        "appointments": [dict(r) for r in ap_rows],
        "conversation_messages": [dict(r) for r in msg_rows],
    }


def _voice_internal_secret_configured() -> str | None:
    s = (os.getenv("VOICE_INTERNAL_SECRET") or "").strip()
    return s or None


def _require_voice_internal(request: Request) -> None:
    secret = _voice_internal_secret_configured()
    if not secret:
        raise HTTPException(status_code=404, detail="Not found")
    got = (request.headers.get("X-Voice-Internal") or "").strip()
    if got != secret:
        raise HTTPException(status_code=403, detail="Forbidden")


class WorkerTranscriptBody(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., min_length=1, max_length=16)
    content: str = Field(..., min_length=1, max_length=32000)


@router.post("/internal/voice/worker/transcript")
def internal_worker_transcript(
    body: WorkerTranscriptBody,
    request: Request,
) -> dict[str, str]:
    """
    Append one transcript line from the trusted LiveKit voice worker (mirrors browser ``conversation_id``).
    Disabled when ``VOICE_INTERNAL_SECRET`` is unset. Requires header ``X-Voice-Internal``.
    """
    _require_voice_internal(request)
    from app.db.conversation_messages import persist_worker_line

    role = body.role.strip().lower()
    try:
        persist_worker_line(
            request.app.state.db_conn,
            session_id=body.conversation_id.strip(),
            role=role,
            content=body.content,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"status": "ok"}


class ToolInvokeBody(BaseModel):
    tool: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/tools/invoke")
def tools_invoke(body: ToolInvokeBody, request: Request) -> dict[str, Any]:
    """Development/agent hook: execute a named tool against the SQLite-backed store."""
    conn = request.app.state.db_conn
    return execute_tool(conn, body.tool, body.arguments)
