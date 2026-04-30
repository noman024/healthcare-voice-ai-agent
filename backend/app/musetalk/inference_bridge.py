from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import yaml

from app.musetalk.config import MuseTalkSettings, load_musetalk_settings

logger = logging.getLogger(__name__)


def musetalk_timing_log_enabled() -> bool:
    return os.getenv("MUSETALK_TIMING_LOG", "").strip().lower() in ("1", "true", "yes", "on")


_gpu_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_rr_idx = 0
_rr_guard = threading.Lock()

_ref_stem_locks: dict[str, threading.Lock] = {}
_ref_stem_guard = threading.Lock()


def _get_ref_stem_lock(stem: str) -> threading.Lock:
    """Serialize jobs that share the same reference face (shared coord .pkl under result_dir)."""
    with _ref_stem_guard:
        if stem not in _ref_stem_locks:
            _ref_stem_locks[stem] = threading.Lock()
        return _ref_stem_locks[stem]


def _get_gpu_lock(gpu_id: int) -> threading.Lock:
    with _locks_guard:
        if gpu_id not in _gpu_locks:
            _gpu_locks[gpu_id] = threading.Lock()
        return _gpu_locks[gpu_id]


def _pick_gpu_round_robin(gpu_ids: tuple[int, ...]) -> int:
    global _rr_idx
    if len(gpu_ids) == 1:
        return gpu_ids[0]
    with _rr_guard:
        g = gpu_ids[_rr_idx % len(gpu_ids)]
        _rr_idx += 1
        return g


def run_lipsync_to_mp4(
    wav_bytes: bytes,
    *,
    settings: MuseTalkSettings | None = None,
    gpu_id: int | None = None,
) -> bytes:
    """
    Run MuseTalk ``scripts/inference.py`` for one reference image + WAV; return muxed MP4 bytes.

    Uses a stable coord cache under ``MUSETALK_CACHE_DIR`` (see MuseTalk ``crop_coord_save_path`` layout).
    """
    s = settings or load_musetalk_settings()
    if not s.enabled:
        raise RuntimeError("MuseTalk is disabled (MUSETALK_ENABLED).")
    if not s.reference_image or not s.reference_image.is_file():
        raise RuntimeError("MUSETALK_REFERENCE_IMAGE must point to an existing portrait (jpg/png).")
    infer = s.root / "scripts" / "inference.py"
    if not infer.is_file():
        raise RuntimeError(f"MuseTalk inference script missing: {infer}")

    gid = int(gpu_id if gpu_id is not None else s.gpu_ids[0])
    ref = s.reference_image.resolve()
    stem = ref.stem

    s.cache_dir.mkdir(parents=True, exist_ok=True)
    result_dir = s.cache_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)

    pkl = s.cache_dir / f"{stem}.pkl"
    use_saved = pkl.is_file()

    job = uuid.uuid4().hex[:16]
    t_wall0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"musetalk_{job}_") as td:
        td_path = Path(td)
        wav_path = td_path / f"{job}.wav"
        wav_path.write_bytes(wav_bytes)
        cfg_path = td_path / "task.yaml"
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "task_0": {
                        "video_path": str(ref),
                        "audio_path": str(wav_path),
                    }
                },
                default_flow_style=False,
            ),
            encoding="utf-8",
        )

        cmd: list[str] = [
            (os.getenv("MUSETALK_PYTHON") or "").strip() or sys.executable,
            str(infer),
            "--inference_config",
            str(cfg_path),
            "--result_dir",
            str(result_dir),
            "--gpu_id",
            str(gid),
            "--version",
            s.version,
            "--batch_size",
            str(s.batch_size),
            "--fps",
            str(os.getenv("MUSETALK_FPS", "25")),
            "--output_vid_name",
            f"{stem}_{job}.mp4",
        ]
        if s.version == "v15":
            cmd += [
                "--unet_config",
                str(s.root / "models" / "musetalkV15" / "musetalk.json"),
                "--unet_model_path",
                str(s.root / "models" / "musetalkV15" / "unet.pth"),
            ]
        else:
            cmd += [
                "--unet_config",
                str(s.root / "models" / "musetalk" / "musetalk.json"),
                "--unet_model_path",
                str(s.root / "models" / "musetalk" / "pytorch_model.bin"),
            ]
        if s.ffmpeg_path:
            cmd += ["--ffmpeg_path", s.ffmpeg_path]
        if s.use_float16:
            cmd.append("--use_float16")
        if use_saved:
            cmd.append("--use_saved_coord")
        else:
            cmd.append("--saved_coord")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(s.root) + os.pathsep + env.get("PYTHONPATH", "")
        # Speed: cuDNN autotune fixed-size convs (safe for stable inference shapes).
        if os.getenv("MUSETALK_TORCH_CUDNN_BENCHMARK", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            env.setdefault("TORCH_CUDNN_BENCHMARK", "1")

        t_prep_done = time.perf_counter()
        logger.info(
            "musetalk_start job=%s use_saved_coord=%s ref=%s gpu=%s batch=%s",
            job,
            use_saved,
            ref,
            gid,
            s.batch_size,
        )
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(s.root),
                env=env,
                capture_output=True,
                timeout=s.timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"MuseTalk timed out after {s.timeout_sec}s") from e

        t_subprocess_done = time.perf_counter()

        err_full = (proc.stderr or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            tail = err_full[-4000:]
            logger.warning("musetalk_failed rc=%s stderr_tail=%s", proc.returncode, tail)
            raise RuntimeError(
                f"MuseTalk inference failed (exit {proc.returncode}): {tail[-1500:].strip() or 'no stderr'}"
            )

        out_dir = result_dir / s.version
        out_mp4 = out_dir / f"{stem}_{job}.mp4"
        if not out_mp4.is_file():
            tail = err_full[-4000:]
            logger.warning("musetalk_missing_output path=%s stderr_tail=%s", out_mp4, tail)
            raise RuntimeError(
                f"MuseTalk output missing: {out_mp4}. Inference stderr tail: {tail[-1500:].strip() or 'no stderr'}"
            )

        data = out_mp4.read_bytes()
        try:
            out_mp4.unlink(missing_ok=True)
        except OSError:
            pass
        sub = out_dir / f"{stem}_{job}"
        if sub.is_dir():
            shutil.rmtree(sub, ignore_errors=True)
        t_end = time.perf_counter()
        if musetalk_timing_log_enabled():
            prep_ms = (t_prep_done - t_wall0) * 1000.0
            sub_ms = (t_subprocess_done - t_prep_done) * 1000.0
            tail_ms = (t_end - t_subprocess_done) * 1000.0
            logger.info(
                "musetalk_latency job=%s gpu=%s prep_ms=%.1f subprocess_ms=%.1f post_ms=%.1f total_ms=%.1f "
                "wav_b=%d mp4_b=%d use_saved_coord=%s batch=%s",
                job,
                gid,
                prep_ms,
                sub_ms,
                tail_ms,
                (t_end - t_wall0) * 1000.0,
                len(wav_bytes),
                len(data),
                use_saved,
                s.batch_size,
            )
        return data


def run_lipsync_to_mp4_locked(wav_bytes: bytes) -> bytes:
    """Schedule inference on a GPU; coordinate file creation is serialized only until the face cache exists.

    After ``{stem}.pkl`` exists (``--use_saved_coord``), jobs on different GPUs no longer share a global
    reference lock — previously every request serialized on ``_get_ref_stem_lock``, negating multi-GPU.
    """
    s = load_musetalk_settings()
    fl = os.getenv("MUSETALK_SINGLE_FLIGHT", "1").strip().lower() in ("1", "true", "yes", "on")
    chosen = _pick_gpu_round_robin(s.gpu_ids)
    ref = s.reference_image
    stem = (ref.stem if ref and ref.name else "reference") or "reference"
    coord_cache_path = s.cache_dir / f"{stem}.pkl"
    coord_ready = coord_cache_path.is_file()

    def _run() -> bytes:
        return run_lipsync_to_mp4(wav_bytes, settings=s, gpu_id=chosen)

    if not fl:
        if coord_ready:
            return _run()
        with _get_ref_stem_lock(stem):
            return _run()
    if coord_ready:
        with _get_gpu_lock(chosen):
            return _run()
    with _get_ref_stem_lock(stem):
        with _get_gpu_lock(chosen):
            return _run()
