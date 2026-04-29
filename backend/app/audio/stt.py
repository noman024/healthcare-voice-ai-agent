from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Union

from faster_whisper import WhisperModel

from app.hardware import whisper_runtime_settings

logger = logging.getLogger(__name__)

_lock = Lock()
_model: WhisperModel | None = None


def reset_whisper_model() -> None:
    """Drop the lazy singleton (tests / device env changes)."""
    global _model
    with _lock:
        _model = None


def _cuda_runtime_bundle_error(exc: BaseException) -> bool:
    """True when CTranslate2/CUDA failed at inference time (e.g. missing libcublas in LD_LIBRARY_PATH)."""
    msg = str(exc).lower()
    needles = (
        "libcublas",
        "libcudnn",
        "libcuda",
        "libcudart",
        "cublas",
        "cannot be loaded",
        "error loading",
        "no cuda gpus",
    )
    return isinstance(exc, RuntimeError) and any(n in msg for n in needles)


def _force_whisper_cpu_env() -> None:
    os.environ["WHISPER_DEVICE"] = "cpu"
    os.environ["WHISPER_COMPUTE_TYPE"] = "int8"


def _whisper_num_workers(device: str, device_index: Union[int, list[int]]) -> int:
    """
    Worker threads inside CTranslate2 for concurrent transcribe() calls.
    With multiple GPUs, default matches GPU count (capped) so parallel requests can use them.
    """
    env = os.getenv("WHISPER_NUM_WORKERS", "").strip()
    if env:
        return max(1, int(env))
    if device != "cuda":
        return max(1, int(os.getenv("WHISPER_CPU_NUM_WORKERS", "1")))
    n_gpu = len(device_index) if isinstance(device_index, list) else 1
    cap = max(1, int(os.getenv("WHISPER_MAX_WORKERS", "8")))
    return max(1, min(cap, n_gpu))


def get_whisper_model() -> WhisperModel:
    """Lazy singleton Whisper model (first call may download weights)."""
    global _model
    with _lock:
        if _model is None:
            size = os.getenv("WHISPER_MODEL", "base")
            device, device_index, ctype = whisper_runtime_settings()
            num_workers = _whisper_num_workers(device, device_index)
            logger.info(
                "loading_whisper model=%s device=%s device_index=%s compute_type=%s num_workers=%s",
                size,
                device,
                device_index,
                ctype,
                num_workers,
            )
            try:
                if device == "cuda":
                    _model = WhisperModel(
                        size,
                        device=device,
                        device_index=device_index,
                        compute_type=ctype,
                        num_workers=num_workers,
                    )
                else:
                    _model = WhisperModel(
                        size,
                        device=device,
                        compute_type=ctype,
                        num_workers=num_workers,
                    )
            except Exception as e:
                if device == "cuda":
                    fb = os.getenv("WHISPER_COMPUTE_TYPE", "").strip() or "int8"
                    nw_cpu = max(1, int(os.getenv("WHISPER_CPU_NUM_WORKERS", "1")))
                    logger.warning(
                        "whisper_cuda_load_failed falling_back_cpu error=%s cpu_compute_type=%s num_workers=%s",
                        e,
                        fb,
                        nw_cpu,
                    )
                    _model = WhisperModel(
                        size,
                        device="cpu",
                        compute_type=fb,
                        num_workers=nw_cpu,
                    )
                else:
                    raise
        return _model


def _vad_filter_enabled() -> bool:
    """When true, pass ``vad_filter=True`` to faster-whisper (silence trimming / fewer hallucinations)."""
    v = os.getenv("WHISPER_VAD_FILTER", "").strip().lower()
    if not v:
        return False
    return v in ("1", "true", "yes", "on")


def transcribe_path(
    path: str | Path,
    *,
    language: str | None = None,
) -> tuple[str, str | None]:
    """
    Transcribe audio file path. Returns (text, detected_language_or_none).
    On total failure returns ("", None) and logs the error (graceful STT failure).
    """
    path = Path(path)
    lang = language if language else None
    beam = int(os.getenv("WHISPER_BEAM_SIZE", "5"))

    def _run(model: WhisperModel) -> tuple[str, str | None]:
        transcribe_kw: dict[str, Any] = {"language": lang, "beam_size": beam}
        if _vad_filter_enabled():
            transcribe_kw["vad_filter"] = True
        segments, info = model.transcribe(str(path), **transcribe_kw)
        parts: list[str] = []
        for seg in segments:
            parts.append(seg.text)
        text = "".join(parts).strip()
        detected = getattr(info, "language", None)
        logger.info("stt_ok duration_approx=%s lang=%s text_len=%s", info.duration, detected, len(text))
        return text, detected

    try:
        return _run(get_whisper_model())
    except RuntimeError as e:
        if _cuda_runtime_bundle_error(e):
            logger.warning(
                "stt_cuda_runtime_failed_reloading_cpu path=%s error=%s "
                "(hint: CTranslate2 often needs libcublas.so.12; CUDA 13 hosts may need a CUDA 12 "
                "compat runtime or set WHISPER_DEVICE=cpu — see README / CUDA_LIBRARY_PATH)",
                path,
                e,
            )
            reset_whisper_model()
            _force_whisper_cpu_env()
            try:
                return _run(get_whisper_model())
            except Exception as e2:
                logger.exception("stt_failed_after_cpu_fallback path=%s error=%s", path, e2)
                return "", None
        logger.exception("stt_failed path=%s error=%s", path, e)
        return "", None
    except Exception as e:
        logger.exception("stt_failed path=%s error=%s", path, e)
        return "", None


def transcribe_file(path: str | Path, *, language: str | None = None) -> str:
    text, _ = transcribe_path(path, language=language)
    return text
