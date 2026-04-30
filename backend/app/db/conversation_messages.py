"""Optional SQLite-backed rolling transcript for SessionMemory."""

from __future__ import annotations

import logging
import os
import sqlite3
from app.agent.memory import SessionMemory

logger = logging.getLogger(__name__)

DEFAULT_MAX_MESSAGES = 20


def persistence_enabled() -> bool:
    v = os.getenv("CONVERSATION_PERSIST", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _max_sql_rows() -> int:
    try:
        n = int(os.getenv("CONVERSATION_PERSIST_MAX_MESSAGES", "").strip())
        if n > 0:
            return n * 2
    except ValueError:
        pass
    return DEFAULT_MAX_MESSAGES



def hydrate_session_memory(mem: SessionMemory, conn: sqlite3.Connection, session_id: str) -> None:
    """Load last N messages from SQLite when memory is empty and persistence is enabled."""
    if not persistence_enabled():
        return
    if len(mem) > 0:
        return

    rows = conn.execute(
        """
        SELECT role, content FROM conversation_messages
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (session_id, _max_sql_rows()),
    ).fetchall()

    if not rows:
        return

    # Oldest first for deque chronological order (rows were fetched newest-first).
    for role, content in reversed(rows):
        mem.append_raw_turn(str(role).strip(), str(content))

    logger.info(
        "conversation_messages_hydrated session=%s count=%s",
        session_id,
        len(rows),
    )


def persist_exchange(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    user_message: str,
    assistant_message: str,
) -> None:
    if not persistence_enabled():
        return
    conn.execute(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, "user", user_message),
    )
    conn.execute(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, "assistant", assistant_message),
    )
    _truncate_session_rows(conn, session_id)
    conn.commit()


def fetch_transcript_text(conn: sqlite3.Connection, session_id: str) -> str:
    """Chronological dialogue as ``role: content`` lines (empty if no rows)."""
    sid = (session_id or "").strip()
    if not sid:
        return ""
    rows = conn.execute(
        """
        SELECT role, content FROM conversation_messages
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (sid,),
    ).fetchall()
    lines: list[str] = []
    for row in rows:
        role = str(row["role"] if isinstance(row, sqlite3.Row) else row[0]).strip()
        content = str(row["content"] if isinstance(row, sqlite3.Row) else row[1]).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def persist_worker_line(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    role: str,
    content: str,
) -> None:
    """Insert one turn line from the trusted LiveKit worker (same truncate rules as REST)."""
    sid = (session_id or "").strip()
    if not sid:
        return
    r = (role or "").strip().lower()
    if r not in ("user", "assistant"):
        raise ValueError("role must be user or assistant")
    text = (content or "").strip()
    if not text:
        return
    conn.execute(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        (sid, r, text),
    )
    _truncate_session_rows(conn, sid)
    conn.commit()


def _truncate_session_rows(conn: sqlite3.Connection, session_id: str) -> None:
    """Retain at most the newest ``CONVERSATION_PERSIST_MAX_MESSAGES`` exchanges (paired rows)."""
    keep = _max_sql_rows()
    conn.execute(
        """
        DELETE FROM conversation_messages
        WHERE session_id = ?
          AND id NOT IN (
              SELECT id FROM (
                  SELECT id FROM conversation_messages
                  WHERE session_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
          )
        """,
        (session_id, session_id, keep),
    )
