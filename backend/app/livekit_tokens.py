"""Issue LiveKit access tokens when ``livekit-api`` is installed and env is set."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def livekit_token_service_enabled() -> bool:
    key = os.getenv("LIVEKIT_API_KEY", "").strip()
    secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
    return bool(key and secret)


def try_build_livekit_token(*, room: str, identity: str, name: str | None = None) -> dict[str, Any]:
    """
    Return ``{"token": jwt, "room": room, "identity": identity}`` or raise ``RuntimeError``.
    """
    if not livekit_token_service_enabled():
        raise RuntimeError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set.")

    try:
        from livekit import api
    except ImportError as e:
        raise RuntimeError(
            "Install LiveKit API helpers: pip install -r requirements-livekit.txt",
        ) from e

    key = os.getenv("LIVEKIT_API_KEY", "").strip()
    secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
    grants = api.VideoGrants(room_join=True, room=room)
    t = (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_grants(grants)
    )
    if name:
        t = t.with_name(name)
    return {"token": t.to_jwt(), "room": room, "identity": identity}
