from __future__ import annotations

from dataclasses import dataclass
import sqlite3


@dataclass
class HealthcareUserdata:
    """Per-room job state shared with @function_tool handlers via RunContext.userdata."""

    conn: sqlite3.Connection
    """SQLite connection (same file as FastAPI — avoid mutating from API mid-call in production)."""
    session_key: str
    """Booking-gate session id: starts as LiveKit participant identity; switches to normalized phone after identify_user."""
