from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.llm.ollama import ollama_base_url
from app.version import APP_VERSION

router = APIRouter(tags=["health"])


def _ollama_tags_get(base_url: str) -> httpx.Response:
    return httpx.get(f"{base_url}/api/tags", timeout=3.0)


@router.get("/")
def root() -> dict[str, str]:
    return {"service": "voice-healthcare-agent", "version": APP_VERSION}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/llm")
def health_llm() -> Any:
    """Probe Ollama for manual diagnostics (no LLM generation)."""
    base = ollama_base_url()
    try:
        r = _ollama_tags_get(base)
        r.raise_for_status()
        return {"ollama": "ok", "base": base}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"ollama": "unavailable", "base": base, "detail": str(e)},
        )
