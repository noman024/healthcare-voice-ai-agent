#!/usr/bin/env python3
"""Run MuseTalk lipsync repeatedly for tuning (batch size, x264, etc.).

Uses ``backend/.env`` but does **not** override an already-set ``MUSETALK_BATCH_SIZE`` (export it to sweep).

Example::

  cd backend
  export MUSETALK_TIMING_LOG=1
  for b in 4 8 12; do MUSETALK_BATCH_SIZE=$b ./scripts/benchmark_musetalk.py --wav /tmp/x.wav; done
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def main() -> int:
    p = argparse.ArgumentParser(description="Benchmark MuseTalk inference (subprocess).")
    p.add_argument("--wav", type=Path, required=True, help="Input WAV path")
    p.add_argument("--repeats", type=int, default=1, ge=1, le=32)
    args = p.parse_args()

    wav_path = args.wav.expanduser().resolve()
    if not wav_path.is_file():
        print("missing wav", wav_path, file=sys.stderr)
        return 2

    os.environ.setdefault("MUSETALK_TIMING_LOG", "1")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from dotenv import load_dotenv

    load_dotenv(_backend / ".env", override=False)

    wav = wav_path.read_bytes()
    from app.musetalk.config import load_musetalk_settings
    from app.musetalk.inference_bridge import run_lipsync_to_mp4_locked

    s = load_musetalk_settings()
    print(
        f"batch={s.batch_size} float16={s.use_float16} gpu_ids={s.gpu_ids} "
        f"x264_preset={os.getenv('MUSETALK_X264_PRESET')!r} x264_crf={os.getenv('MUSETALK_X264_CRF')!r}",
        flush=True,
    )

    for i in range(args.repeats):
        t0 = time.perf_counter()
        mp4 = run_lipsync_to_mp4_locked(wav)
        dt = (time.perf_counter() - t0) * 1000.0
        print(f"run={i + 1} wall_ms={dt:.1f} mp4_bytes={len(mp4)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
