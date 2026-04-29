"""Ensure CUDA shared libs (e.g. libcublas) are on LD_LIBRARY_PATH for Python GPU backends."""

from __future__ import annotations

import glob
import os
from pathlib import Path


def _discover_cuda_lib_dirs() -> list[str]:
    """Collect likely lib64 dirs (CUDA 12 vs 13 layouts, targets tree)."""
    seen: set[str] = set()
    out: list[str] = []

    def add(path: str) -> None:
        p = path.strip()
        if p and Path(p).is_dir() and p not in seen:
            seen.add(p)
            out.append(p)

    raw = os.getenv("CUDA_LIBRARY_PATH", "").strip()
    if raw:
        for part in raw.split(":"):
            add(part)

    home = os.getenv("CUDA_HOME", "").strip()
    if home:
        hp = Path(home)
        for sub in ("lib64", "lib"):
            cand = hp / sub
            if cand.is_dir():
                add(str(cand))
        tgt = hp / "targets" / "x86_64-linux" / "lib"
        if tgt.is_dir():
            add(str(tgt))

    for cand in (
        "/usr/local/cuda/targets/x86_64-linux/lib",
        "/usr/local/cuda/lib64",
    ):
        add(cand)

    for path in sorted(glob.glob("/usr/local/cuda-*/lib64")):
        add(path)

    for cuda_root in sorted(glob.glob("/usr/local/cuda-*")):
        tgt = Path(cuda_root) / "targets" / "x86_64-linux" / "lib"
        if tgt.is_dir():
            add(str(tgt))

    return out


def prepend_cuda_ld_library_path() -> None:
    """
    Prepend discovered CUDA lib dirs to LD_LIBRARY_PATH (after load_dotenv).

    CTranslate2 wheels often link **libcublas.so.12**; some hosts only ship **CUDA 13**
    (libcublas.so.13) under ``targets/x86_64-linux/lib``. Adding those dirs fixes many
    setups; if **.12** is still missing, install the CUDA 12 compatibility runtime or set
    ``WHISPER_DEVICE=cpu``.
    """
    found = _discover_cuda_lib_dirs()
    if not found:
        return
    prepend = ":".join(found)
    prev = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{prepend}:{prev}" if prev else prepend
