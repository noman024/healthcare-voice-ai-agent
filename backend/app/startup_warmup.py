"""Optional heavyweight startup: load Whisper, prime Ollama, run one Piper synthesis."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def warmup_models() -> None:
    """Call from FastAPI lifespan so first user request avoids cold-load latency."""
    mode = os.getenv("WARMUP_MODELS", "1").strip().lower()
    if mode in ("0", "false", "no", "off"):
        logger.info("warmup_skipped WARMUP_MODELS=%s", os.getenv("WARMUP_MODELS"))
        return

    logger.info("warmup_models_start")

    try:
        from app.audio.stt import get_whisper_model

        get_whisper_model()
        logger.info("warmup_whisper_ok")
    except Exception as e:
        logger.warning("warmup_whisper_failed: %s", e)

    try:
        from app.llm import ollama as ollama_mod

        model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct").strip()
        try:
            names = ollama_mod.ollama_list_model_names()
        except Exception as tag_err:
            logger.warning("warmup_ollama_tags_failed: %s (will still try chat)", tag_err)
            names = None
        if names is not None and model not in names:
            logger.warning(
                "warmup_ollama_skipped model=%s not in `ollama list` — run: ollama pull %s",
                model,
                model,
            )
        else:
            _ = ollama_mod.ollama_chat([{"role": "user", "content": "Reply with only the word: ok"}])
            logger.info("warmup_ollama_ok")
    except Exception as e:
        logger.warning("warmup_ollama_failed: %s", e)

    try:
        from app.audio.tts import is_tts_configured, synthesize_wav_bytes

        if is_tts_configured():
            _ = synthesize_wav_bytes("Warm-up.")
            logger.info("warmup_piper_ok")
        else:
            logger.info("warmup_piper_skipped not configured")
    except Exception as e:
        logger.warning("warmup_piper_failed: %s", e)

    logger.info("warmup_models_done")
