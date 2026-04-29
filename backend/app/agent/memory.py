from __future__ import annotations

from collections import deque

_MAX_MESSAGES = 20


class SessionMemory:
    """Rolling history: up to 10 back-and-forth turns (20 chat messages)."""

    def __init__(self) -> None:
        self._messages: deque[dict[str, str]] = deque(maxlen=_MAX_MESSAGES)

    def as_ollama_messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def append_exchange(self, user: str, assistant: str) -> None:
        self._messages.append({"role": "user", "content": user})
        self._messages.append({"role": "assistant", "content": assistant})

    def transcript_text(self) -> str:
        """Plain dialogue text for summarization or logging."""
        lines: list[str] = []
        for m in self._messages:
            role = str(m.get("role", "")).strip()
            content = str(m.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def append_raw_turn(self, role: str, content: str) -> None:
        """Restore one message pair entry (hydration); role is ``user`` or ``assistant``."""
        r = (role or "").strip().lower()
        if r not in ("user", "assistant"):
            return
        self._messages.append({"role": r, "content": (content or "").strip()})

    def __len__(self) -> int:
        return len(self._messages)


_sessions: dict[str, SessionMemory] = {}


def get_session_transcript(session_id: str) -> str:
    """Last N turns for this session as plain text (empty if unknown)."""
    mem = _sessions.get(session_id)
    if mem is None:
        return ""
    return mem.transcript_text()


def get_session_memory(session_id: str) -> SessionMemory:
    s = _sessions.get(session_id)
    if s is None:
        s = SessionMemory()
        _sessions[session_id] = s
    return s


def clear_session_memory_for_tests() -> None:
    _sessions.clear()
