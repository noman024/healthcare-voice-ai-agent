from __future__ import annotations

import os
from typing import Union

from app.hardware.cuda import cuda_gpu_count


def whisper_runtime_settings() -> tuple[str, Union[int, list[int]], str]:
    """
    Resolve (device, device_index, compute_type) for faster-whisper / CTranslate2.

    - WHISPER_DEVICE: `auto` (default), `cuda`, or `cpu`
    - WHISPER_DEVICE_INDICES: comma list e.g. `0,1` — when cuda/auto, defaults to all visible GPUs
    - WHISPER_COMPUTE_TYPE: optional; defaults float16 on GPU, int8 on CPU
    """
    mode = os.getenv("WHISPER_DEVICE", "auto").strip().lower()
    raw_ix = os.getenv("WHISPER_DEVICE_INDICES", "").strip()
    ctype_env = os.getenv("WHISPER_COMPUTE_TYPE", "").strip()

    def parse_indices() -> list[int]:
        if not raw_ix:
            return []
        return [int(x.strip()) for x in raw_ix.split(",") if x.strip() != ""]

    n = cuda_gpu_count()

    def cuda_indices() -> list[int]:
        parsed = parse_indices()
        if parsed:
            return parsed
        if n > 0:
            return list(range(n))
        return []

    if mode == "cpu":
        ctype = ctype_env or "int8"
        return "cpu", 0, ctype

    if mode == "cuda":
        idx = cuda_indices()
        if not idx:
            ctype = ctype_env or "int8"
            return "cpu", 0, ctype
        ctype = ctype_env or "float16"
        device_index: Union[int, list[int]] = idx[0] if len(idx) == 1 else idx
        return "cuda", device_index, ctype

    # auto
    if n > 0:
        idx = cuda_indices()
        ctype = ctype_env or "float16"
        device_index = idx[0] if len(idx) == 1 else idx
        return "cuda", device_index, ctype
    ctype = ctype_env or "int8"
    return "cpu", 0, ctype
