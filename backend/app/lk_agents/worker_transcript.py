"""Mirror LiveKit transcript lines to the FastAPI SQLite-backed store."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)


def voice_internal_secret() -> str:
    return (os.getenv("VOICE_INTERNAL_SECRET") or "").strip()


def post_transcript_line(
    api_base: str,
    secret: str,
    conversation_id: str,
    role: str,
    text: str,
) -> None:
    try:
        r = httpx.post(
            f"{api_base.rstrip('/')}/internal/voice/worker/transcript",
            json={"conversation_id": conversation_id, "role": role, "content": text},
            headers={"X-Voice-Internal": secret},
            timeout=10.0,
        )
        if r.status_code >= 400:
            logger.warning("worker_transcript_http_%s %s", r.status_code, r.text[:200])
    except httpx.HTTPError as e:
        logger.warning("worker_transcript_post_failed %s", e)


async def persist_transcript_line(
    api_base: str,
    secret: str,
    conversation_id: str,
    role: str,
    text: str,
) -> None:
    if not secret or not conversation_id or not text.strip():
        return
    await asyncio.to_thread(post_transcript_line, api_base, secret, conversation_id, role, text.strip())
