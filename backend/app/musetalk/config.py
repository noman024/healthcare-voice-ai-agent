from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _backend_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _repo_root() -> Path:
    """Monorepo root (parent of ``backend``)."""
    return _backend_dir().parent


def _ffmpeg_bin_in_dir(d: Path) -> bool:
    return d.is_dir() and (d / "ffmpeg").is_file()


def _resolve_ffmpeg_dir(raw: str | None) -> str | None:
    r = (raw or "").strip()
    if not r:
        return None
    p = Path(r).expanduser()
    if not p.is_absolute():
        p = (_repo_root() / p).resolve()
    return str(p) if _ffmpeg_bin_in_dir(p) else None


def _default_ffmpeg_dir() -> str | None:
    cur = _repo_root() / "third_party" / "ffmpeg-static" / "current"
    return str(cur.resolve()) if _ffmpeg_bin_in_dir(cur) else None


def _resolve_backend_relative_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (_backend_dir() / p).resolve()
    return p


def _cuda_device_count() -> int | None:
    try:
        import torch

        if torch.cuda.is_available():
            return int(torch.cuda.device_count())
    except Exception:
        return None
    return None


def _parse_gpu_ids() -> tuple[int, ...]:
    raw = (os.getenv("MUSETALK_GPU_IDS") or "").strip()
    if raw:
        ids: list[int] = []
        for part in raw.split(","):
            p = part.strip()
            if not p:
                continue
            try:
                ids.append(int(p))
            except ValueError:
                continue
        if ids:
            return tuple(ids)
    use_all = os.getenv("MUSETALK_USE_ALL_GPUS", "").strip().lower() in ("1", "true", "yes", "on")
    if use_all:
        n = _cuda_device_count()
        if n and n > 0:
            return tuple(range(n))
    try:
        gid = int(os.getenv("MUSETALK_GPU_ID", "0").strip() or "0")
    except ValueError:
        gid = 0
    return (gid,)


@dataclass(frozen=True)
class MuseTalkSettings:
    enabled: bool
    root: Path
    reference_image: Path | None
    cache_dir: Path
    gpu_ids: tuple[int, ...]
    timeout_sec: float
    version: str
    use_float16: bool
    batch_size: int
    ffmpeg_path: str | None


def _default_musetalk_reference_path() -> Path:
    """Conventional portrait path (backend/assets/musetalk/reference.jpg)."""
    return (_backend_dir() / "assets" / "musetalk" / "reference.jpg").resolve()


def load_musetalk_settings() -> MuseTalkSettings:
    en = os.getenv("MUSETALK_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    root_raw = (os.getenv("MUSETALK_ROOT") or "").strip()
    if root_raw:
        root = Path(root_raw).expanduser()
        if not root.is_absolute():
            root = (_backend_dir() / root).resolve()
    else:
        root = (_repo_root() / "third_party" / "MuseTalk").resolve()
    ref_raw = (os.getenv("MUSETALK_REFERENCE_IMAGE") or "").strip()
    if ref_raw:
        candidate = _resolve_backend_relative_path(ref_raw)
        reference = candidate if candidate.is_file() else None
    else:
        reference = None
    default_ref = _default_musetalk_reference_path()
    if reference is None and default_ref.is_file():
        reference = default_ref
    cache_raw = (os.getenv("MUSETALK_CACHE_DIR") or "").strip()
    if cache_raw:
        cache = Path(cache_raw).expanduser()
        if not cache.is_absolute():
            cache = (_backend_dir() / cache).resolve()
        else:
            cache = cache.resolve()
    else:
        cache = (_backend_dir() / ".musetalk_cache").resolve()
    gpu_ids = _parse_gpu_ids()
    try:
        timeout_sec = float(os.getenv("MUSETALK_TIMEOUT_SEC", "240").strip() or "240")
    except ValueError:
        timeout_sec = 240.0
    version = (os.getenv("MUSETALK_VERSION", "v15").strip() or "v15").lower()
    if version not in ("v1", "v15"):
        version = "v15"
    use_f16 = os.getenv("MUSETALK_FLOAT16", "1").strip().lower() in ("1", "true", "yes", "on")
    try:
        bs = max(1, int(os.getenv("MUSETALK_BATCH_SIZE", "4").strip() or "4"))
    except ValueError:
        bs = 4
    ff = _resolve_ffmpeg_dir(os.getenv("MUSETALK_FFMPEG_PATH"))
    if ff is None:
        ff = _default_ffmpeg_dir()
    return MuseTalkSettings(
        enabled=en,
        root=root.resolve(),
        reference_image=reference,
        cache_dir=cache,
        gpu_ids=gpu_ids,
        timeout_sec=timeout_sec,
        version=version,
        use_float16=use_f16,
        batch_size=bs,
        ffmpeg_path=ff,
    )


def musetalk_ffmpeg_available(s: MuseTalkSettings) -> bool:
    if s.ffmpeg_path and _ffmpeg_bin_in_dir(Path(s.ffmpeg_path)):
        return True
    return shutil.which("ffmpeg") is not None


def musetalk_status() -> dict:
    s = load_musetalk_settings()
    ref_ok = bool(s.reference_image and s.reference_image.is_file())
    root_ok = s.root.is_dir() and (s.root / "scripts" / "inference.py").is_file()
    unet = s.root / "models" / "musetalkV15" / "unet.pth"
    unet_v1 = s.root / "models" / "musetalk" / "unet.pth"
    unet_ok = unet.is_file() or unet_v1.is_file()
    whisper = s.root / "models" / "whisper"
    whisper_ok = whisper.is_dir()
    ffmpeg_ok = musetalk_ffmpeg_available(s)
    core = root_ok and ref_ok and unet_ok and whisper_ok and ffmpeg_ok
    ready = s.enabled and core
    hint: str | None = None
    if not s.enabled:
        hint = None
    elif not ready:
        parts: list[str] = []
        if not root_ok:
            parts.append("clone MuseTalk into MUSETALK_ROOT")
        if not ref_ok:
            parts.append("set MUSETALK_REFERENCE_IMAGE to an existing portrait under backend/")
        if not unet_ok or not whisper_ok:
            parts.append("download model weights into MUSETALK_ROOT/models (see README)")
        if not ffmpeg_ok:
            parts.append(
                "install ffmpeg on PATH or run backend/scripts/setup_ffmpeg_static.sh and set MUSETALK_FFMPEG_PATH "
                "to ../third_party/ffmpeg-static/current (repo root relative)"
            )
        hint = "; ".join(parts) if parts else None
    return {
        "enabled": s.enabled,
        "ready": ready,
        "ffmpeg": ffmpeg_ok,
        "root": str(s.root),
        "reference_configured": ref_ok,
        "models_unet": unet_ok,
        "models_whisper_dir": whisper_ok,
        "gpu_ids": list(s.gpu_ids),
        "hint": hint,
    }
