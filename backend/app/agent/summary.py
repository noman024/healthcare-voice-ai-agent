"""Post-call transcript summarization via the same local Ollama stack."""

from __future__ import annotations

import httpx

from app.agent.memory import get_session_transcript
from app.llm import ollama as ollama_client
from app.llm.prompts import SUMMARY_SYSTEM


def summarize_session(
    *,
    session_id: str,
    client: httpx.Client | None = None,
) -> str:
    text = get_session_transcript(session_id.strip() or "default")
    if not text.strip():
        raise ValueError("No conversation recorded for this session_id yet.")

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"Transcript:\n\n{text}"},
    ]
    return ollama_client.ollama_chat(messages, client=client).strip()
