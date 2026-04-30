from __future__ import annotations

import os
from io import BytesIO
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

router = APIRouter(prefix="/avatar", tags=["avatar"])


def musetalk_service_url() -> str:
    return (os.getenv("MUSETALK_SERVICE_URL") or "").strip().rstrip("/")


def musetalk_proxy_timeout() -> float:
    try:
        return float(os.getenv("MUSETALK_PROXY_TIMEOUT_SEC", "300").strip() or "300")
    except ValueError:
        return 300.0


@router.get("/lipsync/status")
async def avatar_lipsync_status() -> Any:
    """
    When ``MUSETALK_SERVICE_URL`` is set (e.g. ``http://127.0.0.1:8001``), forwards to the
    dedicated MuseTalk service; otherwise reports local/in-process MuseTalk status.
    """
    base = musetalk_service_url()
    if base:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                r = await client.get(f"{base}/avatar/lipsync/status")
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"MuseTalk service unreachable ({base}): {e}",
                ) from e
        return JSONResponse(content=r.json(), status_code=r.status_code)
    from app.musetalk.lipsync_api import avatar_lipsync_status_handler

    return await avatar_lipsync_status_handler()


@router.post("/lipsync")
async def avatar_lipsync(request: Request) -> Response:
    """
    When ``MUSETALK_SERVICE_URL`` is set, forwards the multipart upload to that service; otherwise runs
    MuseTalk in this process (same ``MUSETALK_*`` env as the standalone service).
    """
    base = musetalk_service_url()
    form = await request.form()
    audio_f = form.get("audio")
    if audio_f is None:
        raise HTTPException(status_code=422, detail="Missing multipart field 'audio'.")
    content = await audio_f.read()
    filename = getattr(audio_f, "filename", None) or "audio.wav"

    if base:
        content_type = getattr(audio_f, "content_type", None) or "audio/wav"
        async with httpx.AsyncClient(timeout=musetalk_proxy_timeout()) as client:
            try:
                r = await client.post(
                    f"{base}/avatar/lipsync",
                    files={"audio": (filename, content, content_type)},
                )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"MuseTalk service unreachable ({base}): {e}",
                ) from e
        ct = r.headers.get("content-type") or "video/mp4"
        return Response(content=r.content, media_type=ct, status_code=r.status_code)

    if not content:
        raise HTTPException(status_code=422, detail="Empty audio upload.")

    from app.musetalk.lipsync_api import avatar_lipsync_post_handler

    uf = StarletteUploadFile(file=BytesIO(content), filename=filename)
    return await avatar_lipsync_post_handler(uf)


@router.get("/reference")
async def avatar_reference() -> Response:
    """
    Serves ``MUSETALK_REFERENCE_IMAGE`` for the call UI idle portrait. Proxies when ``MUSETALK_SERVICE_URL`` is set.
    """
    base = musetalk_service_url()
    if base:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                r = await client.get(f"{base}/avatar/reference")
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"MuseTalk service unreachable ({base}): {e}",
                ) from e
        ct = r.headers.get("content-type") or "image/jpeg"
        return Response(content=r.content, media_type=ct, status_code=r.status_code)
    from app.musetalk.lipsync_api import avatar_reference_image_handler

    return avatar_reference_image_handler()
