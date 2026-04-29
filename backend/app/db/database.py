from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('booked', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(date, time)
);

CREATE INDEX IF NOT EXISTS idx_appointments_phone ON appointments(phone);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_session_id_id
ON conversation_messages(session_id, id);
"""


def get_db_path() -> Path:
    raw = os.getenv("DATABASE_PATH", "data/appointments.db")
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or get_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
