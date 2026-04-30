from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any


@dataclass
class HealthcareUserdata:
    """Per-room job state shared with @function_tool handlers via RunContext.userdata."""

    conn: sqlite3.Connection
    """SQLite connection (same file as FastAPI — avoid mutating from API mid-call in production)."""
    session_key: str
    """Booking-gate session id: starts as LiveKit participant identity; switches to normalized phone after identify_user."""
    conversation_id: str | None = None
    """Browser tab/session key for SQLite transcript + `POST /agent/summary` (from participant metadata)."""
    room: Any | None = None
    """LiveKit ``Room`` for worker → browser data messages (tool status). Set in ``entrypoint``."""
    ui_dest_identity: str = ""
    """Participant identity to receive ``topic=va`` payloads (browser client)."""
