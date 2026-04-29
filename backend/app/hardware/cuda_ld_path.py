"""Ensure CUDA shared libs (e.g. libcublas) are on LD_LIBRARY_PATH for Python GPU backends."""

from __future__ import annotations

import glob
import os
import site
import sys
from pathlib import Path


def _discover_nvidia_pip_lib_dirs() -> list[str]:
    """
    Pip wheels (nvidia-cublas-cu12, etc.) install under site-packages/nvidia/*/lib.
    CTranslate2's GPU build expects **libcublas.so.12** — those dirs often hold it even when
    ``/usr/local/cuda`` only exposes a newer soname.
    """
    seen: set[str] = set()
    out: list[str] = []

    def add(path: str) -> None:
        p = path.strip()
        if p and Path(p).is_dir():
            rp = str(Path(p).resolve())
            if rp not in seen:
                seen.add(rp)
                out.append(rp)

    bases: list[str] = []
    try:
        bases.extend(site.getsitepackages())
    except Exception:
        pass
    u = ""
    try:
        u = site.getusersitepackages()
    except Exception:
        pass
    if u:
        bases.append(u)
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    for prefix in (sys.prefix, getattr(sys, "base_prefix", sys.prefix)):
        pp = Path(prefix)
        for rel in (
            f"local/lib/python{ver}/site-packages",
            f"lib/python{ver}/site-packages",
            "local/lib/site-packages",
        ):
            cand = pp / rel
            if cand.is_dir():
                bases.append(str(cand))

    subdirs = (
        "nvidia/cublas/lib",
        "nvidia/cublaslt/lib",
        "nvidia/cudnn/lib",
        "nvidia/cuda_runtime/lib",
        "nvidia/cuda_nvrtc/lib",
        "nvidia/cusparse/lib",
        "nvidia/cufft/lib",
    )
    for base in bases:
        bp = Path(base)
        if not bp.is_dir():
            continue
        for sub in subdirs:
            add(str(bp / sub))
    return out


def _discover_conda_lib_dirs() -> list[str]:
    prefix = os.getenv("CONDA_PREFIX", "").strip()
    if not prefix:
        return []
    out: list[str] = []
    for sub in ("lib", "lib64"):
        p = Path(prefix) / sub
        if p.is_dir():
            out.append(str(p.resolve()))
    return out


def _discover_cuda_lib_dirs() -> list[str]:
    """Collect likely lib64 dirs (pip NVIDIA stack, CUDA toolkit, distro layouts)."""
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

    for p in _discover_nvidia_pip_lib_dirs():
        add(p)

    for p in _discover_conda_lib_dirs():
        add(p)

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

    # Debian/Ubuntu packages often place CUDA compatibility libs here
    add("/usr/lib/x86_64-linux-gnu")

    return out


def prepend_cuda_ld_library_path() -> None:
    """
    Prepend discovered CUDA lib dirs to LD_LIBRARY_PATH (after load_dotenv).

    Order: explicit ``CUDA_LIBRARY_PATH``, pip ``site-packages/nvidia/*/lib`` (CUDA 12
    compat from PyPI), Conda ``CONDA_PREFIX/lib``, then ``CUDA_HOME`` / ``/usr/local/cuda``.
    CTranslate2 still needs matching **libcublas** at runtime; if none of these contain it,
    install ``nvidia-cublas-cu12`` in the same venv or set ``CUDA_LIBRARY_PATH`` manually,
    or use ``WHISPER_DEVICE=cpu``.
    """
    found = _discover_cuda_lib_dirs()
    if not found:
        return
    prepend = ":".join(found)
    prev = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{prepend}:{prev}" if prev else prepend
