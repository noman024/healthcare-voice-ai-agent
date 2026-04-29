from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def ollama_base_url() -> str:
    """
    Base URL for Ollama HTTP API (no path suffix).

    If OLLAMA_BASE_URL mistakenly ends with ``/api`` or ``/v1``, strip it so
    requests hit ``/api/chat`` not ``/api/api/chat`` (which returns 404).
    """
    raw = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
    for suffix in ("/api", "/v1"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)].rstrip("/")
    return raw


def ollama_list_model_names(base: str | None = None, *, timeout: float = 10.0) -> set[str]:
    """Names from GET /api/tags (raises if Ollama unreachable or non-2xx)."""
    b = ollama_base_url() if base is None else base.rstrip("/")
    r = httpx.get(f"{b}/api/tags", timeout=timeout)
    r.raise_for_status()
    return {str(m["name"]) for m in r.json().get("models", []) if m.get("name")}


def ollama_model_is_available(model: str | None = None, *, base: str | None = None) -> bool:
    """True if ``OLLAMA_MODEL`` (or given tag) appears in GET /api/tags."""
    tag = (model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")).strip()
    try:
        return tag in ollama_list_model_names(base)
    except Exception:
        return False


def _ollama_chat_options() -> dict[str, Any]:
    """
    Per-request options for Ollama /api/chat.

    - OLLAMA_INFER_DEVICE: `auto` (default), `cpu` (force num_gpu=0), or `gpu` (max layers on GPU).
    - OLLAMA_NUM_GPU_LAYERS: layer offload count; default -1 means “as many as Ollama will place on GPU”.
    - OLLAMA_OPTIONS_JSON: JSON object merged last (overrides keys above if duplicate).
    """
    opts: dict[str, Any] = {}
    raw = os.getenv("OLLAMA_OPTIONS_JSON", "").strip()
    if raw:
        opts.update(json.loads(raw))
    if "num_gpu" not in opts:
        infer = os.getenv("OLLAMA_INFER_DEVICE", "auto").strip().lower()
        if infer == "cpu":
            opts["num_gpu"] = 0
        elif infer in ("auto", "cuda", "gpu", ""):
            layers = os.getenv("OLLAMA_NUM_GPU_LAYERS", "-1").strip() or "-1"
            opts["num_gpu"] = int(layers)
    return opts


def ollama_chat(
    messages: list[dict[str, Any]],
    *,
    client: httpx.Client | None = None,
    timeout_s: float | None = None,
    response_format: str | None = None,
    model: str | None = None,
) -> str:
    """POST /api/chat to a local Ollama server; returns assistant message content.

    ``model`` overrides ``OLLAMA_MODEL`` when set (used by the agent for planner vs finalize tags).
    """
    if timeout_s is None:
        timeout_s = float(os.getenv("OLLAMA_REQUEST_TIMEOUT_S", "300"))
    base = ollama_base_url()
    base_tag = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct").strip()
    model = (model or "").strip() or base_tag
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if response_format:
        payload["format"] = response_format
    opts = _ollama_chat_options()
    if opts:
        payload["options"] = opts

    close_client = False
    if client is None:
        client = httpx.Client(timeout=timeout_s)
        close_client = True
    try:
        resp = client.post(f"{base}/api/chat", json=payload)
        if resp.status_code == 404:
            body = resp.text[:400] if resp.text else ""
            logger.warning(
                "ollama_chat_404 model=%s base=%s hint=pull model or fix OLLAMA_BASE_URL (host:port only, no /api). body=%s",
                model,
                base,
                body,
            )
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, str):
            raise ValueError(f"Unexpected Ollama response: {json.dumps(data)[:500]}")
        text = content.strip()
        if not text:
            raise ValueError("Ollama returned empty message content")
        return text
    finally:
        if close_client:
            client.close()
