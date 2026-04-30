"""Shared MuseTalk HTTP handlers (used by the dedicated service and testable in isolation)."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import time
from typing import Any

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

logger = logging.getLogger(__name__)


async def avatar_lipsync_status_handler() -> dict[str, Any]:
    from app.musetalk.config import musetalk_status

    return musetalk_status()


def avatar_reference_image_handler() -> FileResponse:
    """Serve ``MUSETALK_REFERENCE_IMAGE`` for idle avatar in the browser (same face as inference)."""
    from app.musetalk.config import load_musetalk_settings

    s = load_musetalk_settings()
    path = s.reference_image
    if path is None or not path.is_file():
        logger.warning(
            "avatar_reference_missing MUSETALK_REFERENCE_IMAGE=%s resolved=%s",
            (os.getenv("MUSETALK_REFERENCE_IMAGE") or "").strip() or "(unset)",
            path,
        )
        raise HTTPException(
            status_code=404,
            detail="MuseTalk reference image not found. Set MUSETALK_REFERENCE_IMAGE or add backend/assets/musetalk/reference.jpg.",
        )
    media_type, _ = mimetypes.guess_type(str(path))
    return FileResponse(
        path,
        media_type=media_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def avatar_lipsync_post_handler(audio: UploadFile = File(...)) -> Response:
    from app.musetalk.config import load_musetalk_settings, musetalk_status
    from app.musetalk.inference_bridge import musetalk_timing_log_enabled, run_lipsync_to_mp4_locked

    s = load_musetalk_settings()
    if not s.enabled:
        raise HTTPException(status_code=503, detail="MuseTalk disabled (set MUSETALK_ENABLED=1 on this service).")
    st = musetalk_status()
    if not st.get("ready"):
        raise HTTPException(
            status_code=503,
            detail=st.get("hint") or "MuseTalk not ready — install weights and reference image.",
        )
    t0 = time.perf_counter()
    data = await audio.read()
    t1 = time.perf_counter()
    if not data:
        raise HTTPException(status_code=422, detail="Empty audio upload.")
    try:
        mp4 = await asyncio.to_thread(run_lipsync_to_mp4_locked, data)
    except RuntimeError as e:
        logger.warning("musetalk_inference_failed %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    t2 = time.perf_counter()
    if musetalk_timing_log_enabled():
        logger.info(
            "musetalk_http upload_ms=%.1f infer_total_ms=%.1f wall_ms=%.1f wav_b=%d mp4_b=%d",
            (t1 - t0) * 1000.0,
            (t2 - t1) * 1000.0,
            (t2 - t0) * 1000.0,
            len(data),
            len(mp4),
        )
    return Response(content=mp4, media_type="video/mp4")
