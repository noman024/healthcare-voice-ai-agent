"""CUDA / GPU visibility for CTranslate2-backed models."""

from __future__ import annotations


def cuda_gpu_count() -> int:
    """Number of CUDA devices visible to CTranslate2 (0 if no CUDA / drivers)."""
    try:
        import ctranslate2 as ct

        return int(ct.get_cuda_device_count())
    except Exception:
        return 0


def cuda_device_indices() -> list[int]:
    """0-based indices for each CUDA device CTranslate2 sees (empty if none)."""
    n = cuda_gpu_count()
    return list(range(n)) if n else []
