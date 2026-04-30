"""
MuseTalk-only HTTP server — run on a separate port (e.g. 8001) while the main API stays on 8000.

    cd backend && uvicorn app.musetalk.service_app:app --host 0.0.0.0 --port 8001

Run on a **separate port** (e.g. 8001) using the **main backend venv** (``backend/.venv``). Heavy inference runs under ``MUSETALK_PYTHON`` (see ``backend/.env``). After ``pip install -r third_party/MuseTalk/requirements.txt``, **uninstall TensorFlow** (see README) — it is not used for inference and can crash ``diffusers`` imports.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.hardware.cuda_ld_path import prepend_cuda_ld_library_path

_backend_dir = Path(__file__).resolve().parent.parent.parent
load_dotenv(_backend_dir / ".env", override=True)
load_dotenv()
prepend_cuda_ld_library_path()


async def _warmup_musetalk_async() -> None:
    """Load Torch/Whisper/VAE once so the first user lip-sync is not tens of seconds slower."""
    import asyncio
    import io
    import logging
    import wave

    log = logging.getLogger("musetalk.service")
    try:
        from app.musetalk.config import load_musetalk_settings
        from app.musetalk.inference_bridge import run_lipsync_to_mp4_locked

        if not load_musetalk_settings().enabled:
            log.info("musetalk_warmup_skipped disabled")
            return
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x00\x00" * 24_000)
        await asyncio.to_thread(run_lipsync_to_mp4_locked, buf.getvalue())
        log.info("musetalk_warmup_ok")
    except Exception as e:
        log.warning("musetalk_warmup_failed %s", e)


def _parse_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("MUSETALK_WARMUP_ON_START", "").strip().lower() in ("1", "true", "yes", "on"):
        import asyncio

        asyncio.create_task(_warmup_musetalk_async())
    yield


app = FastAPI(
    title="MuseTalk lip-sync service",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "musetalk"}


@app.get("/avatar/lipsync/status")
async def avatar_lipsync_status() -> dict[str, Any]:
    from app.musetalk.lipsync_api import avatar_lipsync_status_handler

    return await avatar_lipsync_status_handler()


@app.get("/avatar/reference")
def avatar_reference() -> FileResponse:
    from app.musetalk.lipsync_api import avatar_reference_image_handler

    return avatar_reference_image_handler()


@app.post("/avatar/lipsync")
async def avatar_lipsync(audio: UploadFile = File(...)) -> Any:
    from app.musetalk.lipsync_api import avatar_lipsync_post_handler

    return await avatar_lipsync_post_handler(audio)
