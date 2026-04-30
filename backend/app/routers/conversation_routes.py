from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

router = APIRouter(tags=["conversation"])


class ProcessBody(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field(default="default", max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    return_speech: bool = False


@router.post("/process")
def process_endpoint(body: ProcessBody, request: Request) -> dict[str, Any]:
    """Text → agent (LLM + tools) → optional spoken response as base64 WAV."""
    from app.conversation.pipeline import process_text_message

    try:
        return process_text_message(
            request.app.state.db_conn,
            message=body.message,
            session_id=(body.session_id.strip() or "default"),
            return_speech=body.return_speech,
            conversation_id=(
                body.conversation_id.strip()
                if isinstance(body.conversation_id, str) and body.conversation_id.strip()
                else None
            ),
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM service error: {e}") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/conversation")
async def conversation_endpoint(
    request: Request,
    audio: UploadFile | None = File(None),
    session_id: str = Form("default"),
    conversation_id: str | None = Form(None),
    language: str | None = Form(None),
    return_speech: bool = Form(True),
    message: str | None = Form(None),
) -> dict[str, Any]:
    """
    Multipart-only: send either field `audio` (file: STT → agent) or `message` (string: text → agent).
    When `return_speech` is true and Piper is configured, response includes `audio_wav_base64`.
    """
    from app.conversation.pipeline import process_audio_bytes, process_text_message

    sid = (session_id or "").strip() or "default"
    cid = (conversation_id or "").strip() or None
    lang = (language or "").strip() or None

    try:
        if audio is not None and (audio.filename or "").strip():
            data = await audio.read()
            if not data:
                raise HTTPException(status_code=422, detail="Empty audio upload.")
            suffix = Path(audio.filename or "clip").suffix.lower()
            if suffix not in {".wav", ".webm", ".mp3", ".ogg", ".flac", ".m4a", ".mp4", ""}:
                suffix = ".wav"
            return process_audio_bytes(
                request.app.state.db_conn,
                audio_bytes=data,
                file_suffix=suffix,
                session_id=sid,
                language=lang,
                return_speech=return_speech,
                conversation_id=cid,
            )

        msg = (message or "").strip()
        if msg:
            return process_text_message(
                request.app.state.db_conn,
                message=msg,
                session_id=sid,
                return_speech=return_speech,
                conversation_id=cid,
            )

        raise HTTPException(
            status_code=422,
            detail="Provide multipart field `audio` (file) or non-empty `message` (string).",
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM service error: {e}") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
