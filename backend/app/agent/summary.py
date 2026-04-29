"""Post-call transcript summarization with optional SQLite hydration and appointment snapshot."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

import httpx

from app.agent.memory import get_session_memory
from app.db import appointments as appt_repo
from app.db.conversation_messages import hydrate_session_memory
from app.llm import ollama as ollama_client
from app.llm.prompts import SUMMARY_STRUCTURED_SYSTEM
from app.tools.validation import ToolValidationError, normalize_phone

logger = logging.getLogger(__name__)


def _resolve_lookup_phone(session_id: str, phone_override: str | None) -> str | None:
    if phone_override and str(phone_override).strip():
        try:
            return normalize_phone(str(phone_override).strip())
        except ToolValidationError:
            pass
    try:
        return normalize_phone((session_id or "").strip())
    except ToolValidationError:
        return None


def _parse_summary_json(raw: str) -> tuple[str, list[str]]:
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            nar = obj.get("narrative") or obj.get("summary") or ""
            prefs = obj.get("user_preferences") or obj.get("preferences") or []
            if not isinstance(nar, str):
                nar = str(nar) if nar else ""
            if not isinstance(prefs, list):
                prefs = []
            prefs_out = [str(p).strip() for p in prefs if str(p).strip()]
            body = (nar.strip() or raw.strip()).strip()
            return body, prefs_out
    except json.JSONDecodeError:
        logger.warning("summary_json_parse_fallback len=%s", len(raw))
    return raw.strip(), []


def build_agent_summary(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    conversation_id: str | None = None,
    phone: str | None = None,
    client: httpx.Client | None = None,
    include_cost_hints: bool = False,
) -> dict[str, Any]:
    """Load transcript (hydrate from DB when persistence is enabled), attach DB appointments, LLM narrative + preferences."""
    tid = (conversation_id or "").strip() or (session_id.strip() or "default")
    mem = get_session_memory(tid)
    hydrate_session_memory(mem, conn, tid)
    transcript = mem.transcript_text()
    if not transcript.strip():
        raise ValueError("No conversation recorded for this session_id yet.")

    lookup_phone = _resolve_lookup_phone(session_id, phone)
    appointments: list[dict[str, Any]] = []
    if lookup_phone:
        for a in appt_repo.list_appointments_for_phone(conn, lookup_phone, include_cancelled=True):
            appointments.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "phone": a.phone,
                    "date": a.date,
                    "time": a.time,
                    "status": a.status,
                    "created_at": a.created_at,
                },
            )

    appt_block = json.dumps(appointments, indent=2)
    user_msg = (
        f"Transcript:\n\n{transcript}\n\n"
        f"Database appointments for this caller (authoritative):\n{appt_block}\n\n"
        "Output JSON only: narrative (string), user_preferences (array of strings)."
    )

    summary_model = (os.getenv("OLLAMA_SUMMARY_MODEL") or "").strip() or None
    raw_llm = ollama_client.ollama_chat(
        [
            {"role": "system", "content": SUMMARY_STRUCTURED_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        client=client,
        response_format="json",
        model=summary_model,
    )
    narrative, user_preferences = _parse_summary_json(raw_llm)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out: dict[str, Any] = {
        "session_id": (session_id.strip() or "default"),
        "conversation_id": tid,
        "summary": narrative,
        "generated_at": generated_at,
        "phone": lookup_phone,
        "appointments": appointments,
        "user_preferences": user_preferences,
    }
    if include_cost_hints:
        out["cost_hints"] = {
            "summary_ollama_calls": 1,
            "note": "Local Ollama has no per-token billing; STT/TTS usage is per turn on the main call path.",
        }
    return out
