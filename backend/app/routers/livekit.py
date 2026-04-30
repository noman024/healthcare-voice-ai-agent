from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/livekit", tags=["livekit"])


@router.get("/token")
def livekit_token(
    room: str = "healthcare-demo",
    identity: str = "web-user",
    name: str | None = None,
) -> Any:
    """
    Mint a short-lived JWT for the browser LiveKit client when env keys are set and ``livekit-api`` is installed.
    Falls back to HTTP 503 — REST and WebSocket agents remain the primary path.
    """
    from app.livekit_tokens import livekit_token_service_enabled, try_build_livekit_token

    if not livekit_token_service_enabled():
        return JSONResponse(
            status_code=503,
            content={"detail": "LiveKit disabled: set LIVEKIT_API_KEY and LIVEKIT_API_SECRET on the API."},
        )
    try:
        r = (room or "").strip() or "healthcare-demo"
        ident = (identity or "").strip() or "web-user"
        return try_build_livekit_token(room=r, identity=ident, name=(name or "").strip() or None)
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"detail": str(e)})


@router.get("/status")
def livekit_status() -> dict[str, bool]:
    """
    Whether this API instance can mint LiveKit tokens (env keys installed).
    Browsers skip a failing token probe when ``false``.
    """
    from app.livekit_tokens import livekit_token_service_enabled

    return {"token_service_enabled": livekit_token_service_enabled()}
