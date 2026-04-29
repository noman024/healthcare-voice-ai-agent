from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Lock

from app.hardware.cuda import cuda_gpu_count

logger = logging.getLogger(__name__)

_tts_gpu_lock = Lock()
_tts_gpu_rr = 0


class TTSError(RuntimeError):
    """TTS backend missing or Piper failed."""


def is_tts_configured() -> bool:
    voice = os.getenv("PIPER_VOICE", "").strip()
    return bool(voice) and Path(voice).is_file()


def _resolve_piper_binary(binary: str) -> str:
    p = Path(binary)
    if p.is_file():
        return str(p.resolve())
    w = shutil.which(binary)
    if w:
        return str(Path(w).resolve())
    return binary


def _pick_piper_cuda_visible_device() -> str | None:
    """
    Return CUDA_VISIBLE_DEVICES for this Piper subprocess, or None to expose **all** GPUs.

    - PIPER_CUDA=off — no GPU hint (CPU / default ORT behavior).
    - PIPER_CUDA_STRATEGY=all (default when GPUs exist) — do **not** pin; Piper/ONNX sees every GPU.
    - PIPER_CUDA_STRATEGY=round_robin — pin one GPU per request (load-spread across concurrent /tts).
    """
    mode = os.getenv("PIPER_CUDA", "auto").strip().lower()
    if mode in ("0", "off", "false", "cpu", "no"):
        return None
    n = cuda_gpu_count()
    if n <= 0:
        return None

    strategy = os.getenv("PIPER_CUDA_STRATEGY", "all").strip().lower()
    if strategy in ("round_robin", "rr"):
        global _tts_gpu_rr
        with _tts_gpu_lock:
            idx = _tts_gpu_rr % n
            _tts_gpu_rr += 1
        return str(idx)
    # `all` and anything else: leave CUDA_VISIBLE_DEVICES unset
    return None


def _piper_subprocess_env(binary_resolved: str, *, skip_cuda: bool = False) -> dict[str, str]:
    """Prepend Piper's lib dir (bundled .so) to LD_LIBRARY_PATH on Linux."""
    env = os.environ.copy()
    override = os.getenv("PIPER_LD_LIBRARY_PATH", "").strip()
    if override:
        lib_dir = override
    elif Path(binary_resolved).is_file():
        lib_dir = str(Path(binary_resolved).resolve().parent)
    else:
        lib_dir = ""
    if lib_dir:
        prev = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{lib_dir}:{prev}" if prev else lib_dir
    if skip_cuda:
        env.pop("CUDA_VISIBLE_DEVICES", None)
    else:
        vis = _pick_piper_cuda_visible_device()
        if vis is not None:
            env["CUDA_VISIBLE_DEVICES"] = vis
    return env


def synthesize_wav_bytes(text: str) -> bytes:
    """
    Run Piper CLI to produce a WAV file. Requires:
    - `PIPER_VOICE`: path to `.onnx` model (and `.onnx.json` alongside, per Piper)
    - optional `PIPER_BINARY`: path to `piper` (default: resolve via PATH)
    - optional `PIPER_LD_LIBRARY_PATH`: dir containing Piper shared libs (default: Piper binary dir)
    """
    text = text.strip()
    if not text:
        raise TTSError("TTS text is empty.")

    voice = os.getenv("PIPER_VOICE", "").strip()
    if not voice:
        raise TTSError("Set PIPER_VOICE to your Piper .onnx model path.")
    vpath = Path(voice)
    if not vpath.is_file():
        raise TTSError(f"Piper voice file not found: {voice}")

    raw_bin = os.getenv("PIPER_BINARY", "piper")
    binary = _resolve_piper_binary(raw_bin)

    def _run_piper(env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [binary, "--model", str(vpath.resolve()), "--output_file", str(out_path)],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=int(os.getenv("PIPER_TIMEOUT_SEC", "120")),
            env=env,
        )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        proc = _run_piper(_piper_subprocess_env(binary, skip_cuda=False))
        if proc.returncode != 0 and os.getenv("PIPER_CUDA_CPU_FALLBACK", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        ):
            err0 = proc.stderr.decode("utf-8", errors="replace")[:400]
            logger.warning("piper_failed_retry_cpu stderr_prefix=%s", err0)
            proc = _run_piper(_piper_subprocess_env(binary, skip_cuda=True))
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace") or proc.stdout.decode(
                "utf-8",
                errors="replace",
            )
            logger.error("piper_failed code=%s stderr=%s", proc.returncode, err[:2000])
            raise TTSError(f"Piper exited {proc.returncode}: {err[:500]}")
        data = out_path.read_bytes()
        if not data:
            raise TTSError("Piper produced empty WAV.")
        return data
    finally:
        out_path.unlink(missing_ok=True)
