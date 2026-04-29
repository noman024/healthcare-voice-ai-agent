"""Control + event topics for FastAPI-aligned LiveKit data packets."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Reliable data channel topic — must match frontend ``publishData(..., { topic })``.
DEFAULT_AGENT_DATA_TOPIC = "lk-agent-v1"


def normalize_topic(raw: str | None) -> str:
    return (raw or "").strip()


def encode_control_ping() -> bytes:
    return json.dumps({"action": "ping"}).encode("utf-8")


def parse_control_payload(data: bytes) -> tuple[str, dict[str, Any] | None]:
    """Return (verb, parsed dict or None on parse error). Verb: ping | start | finalize | unknown."""
    if not data:
        return ("unknown", None)
    try:
        s = data.decode("utf-8").strip()
        obj = json.loads(s)
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.debug("livekit_agent_non_json_payload len=%s", len(data))
        return ("unknown", None)

    if not isinstance(obj, dict):
        return ("unknown", obj if isinstance(obj, dict) else None)

    action = str(obj.get("action") or "").strip().lower()
    if action == "ping":
        return ("ping", obj)

    lk = obj.get("lk_agent")
    if lk is not None:
        act = str(lk).strip().lower()
        if act == "start":
            return ("start", obj)
        if act == "finalize":
            return ("finalize", obj)

    # Same wire format as /ws/conversation_audio (JSON text control frames)
    if action == "start":
        return ("start", obj)
    if action == "finalize":
        return ("finalize", obj)

    return ("unknown", obj)


def summarize_control(obj: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize start/final shape for pipeline (mirrors websocket handler semantics)."""
    if not obj:
        return {
            "session_id": "default",
            "conversation_id": None,
            "language": None,
            "return_speech": True,
            "file_extension": ".wav",
        }
    sid = str(obj.get("session_id") or "default").strip() or "default"
    cid_raw = obj.get("conversation_id")
    cid = str(cid_raw).strip() if cid_raw not in (None, "") else None
    lang_raw = obj.get("language")
    lang = str(lang_raw).strip() if lang_raw not in (None, "") else None
    return_speech = bool(obj.get("return_speech", True))
    ext = str(obj.get("file_extension") or ".webm").strip() or ".webm"
    if not ext.startswith("."):
        ext = f".{ext}"
    return {
        "session_id": sid,
        "conversation_id": cid,
        "language": lang,
        "return_speech": return_speech,
        "file_extension": ext,
    }
