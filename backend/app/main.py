import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.hardware.cuda_ld_path import prepend_cuda_ld_library_path
from app.log_setup import setup_repo_file_logging
from app.routers import agent_routes, audio_routes, avatar, conversation_routes, health, internal, livekit, websockets
from app.version import APP_VERSION

_backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(_backend_dir / ".env")
load_dotenv()  # optional overrides from cwd
prepend_cuda_ld_library_path()

logger = logging.getLogger(__name__)
_log_file_path = setup_repo_file_logging()
if _log_file_path:
    logger.info("file_logging path=%s", _log_file_path)


def _parse_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db.database import connect, init_db
    from app.startup_warmup import warmup_models

    conn = connect()
    init_db(conn)
    app.state.db_conn = conn
    if os.getenv("WARMUP_MODELS", "1").strip().lower() not in ("0", "false", "no"):
        await asyncio.to_thread(warmup_models)
    yield
    conn.close()


app = FastAPI(
    title="Voice Healthcare Agent API",
    version=APP_VERSION,
    description="Backend for STT, LLM, tools, TTS, and conversation (phased rollout).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(livekit.router)
app.include_router(internal.router)
app.include_router(agent_routes.router)
app.include_router(websockets.router)
app.include_router(conversation_routes.router)
app.include_router(audio_routes.router)
app.include_router(avatar.router)
