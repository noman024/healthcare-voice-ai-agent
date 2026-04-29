#!/usr/bin/env python3
"""
Exercise every production HTTP endpoint with timing (sequential + optional concurrency).

Usage (API + Ollama + Piper + Whisper must be up):
  cd backend && source .venv/bin/activate
  python scripts/benchmark_api_performance.py
  python scripts/benchmark_api_performance.py --url http://127.0.0.1:8000 --rounds 5 --concurrent 8
  python scripts/benchmark_api_performance.py --fail-if-any-route-mean-ms-above 45000   # scripted SLA gate vs warm stack
"""
from __future__ import annotations

import argparse
import io
import math
import statistics
import struct
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000"

# (label, callable(client, round_index) -> (status, ms))
BenchFn = Callable[[httpx.Client, int], tuple[int, float]]


def _wav_bytes_mono_16k_half_sec() -> bytes:
    buf = io.BytesIO()
    fr, n = 16000, 8000
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        frames = bytearray()
        for i in range(n):
            x = int(2500 * math.sin(2 * math.pi * 440 * i / fr))
            frames += struct.pack("<h", max(-32768, min(32767, x)))
        w.writeframes(frames)
    return buf.getvalue()


def _time_request(
    client: httpx.Client,
    method: str,
    url: str,
    **kwargs: Any,
) -> tuple[int, float]:
    t0 = time.perf_counter()
    r = client.request(method, url, **kwargs)
    dt = (time.perf_counter() - t0) * 1000.0
    return r.status_code, dt


def _timing_agent_summary(client: httpx.Client, api_base: str, r_idx: int) -> tuple[int, float]:
    """Prime session with one turn, then time POST /agent/summary only if turn succeeded."""
    sid = f"bench-summary-r{r_idx}-{time.time_ns()}"
    code, ms_turn = _time_request(
        client,
        "POST",
        f"{api_base}/agent/turn",
        json={"message": "Say hello briefly.", "session_id": sid},
    )
    if code != 200:
        return code, ms_turn
    return _time_request(client, "POST", f"{api_base}/agent/summary", json={"session_id": sid})


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark voice-agent APIs (real calls, no mocks).")
    ap.add_argument("--url", default=DEFAULT_BASE, help="API base URL")
    ap.add_argument("--rounds", type=int, default=3, help="Repeats for each endpoint (mean/min/max)")
    ap.add_argument("--concurrent", type=int, default=0, help="If >0, run N parallel GET /health rounds")
    ap.add_argument("--timeout", type=float, default=300.0, help="Per-request timeout (seconds)")
    ap.add_argument(
        "--fail-if-any-route-mean-ms-above",
        type=float,
        default=0.0,
        help=(
            "After sequential benchmarks exit 1 if any route mean (ms) exceeds this. "
            "0 disables this gate — use for scripted smoke SLA against a warm stack."
        ),
    )
    args = ap.parse_args()
    base = args.url.rstrip("/")
    wav = _wav_bytes_mono_16k_half_sec()

    sequential: list[tuple[str, BenchFn]] = [
        ("GET /", lambda c, _: _time_request(c, "GET", f"{base}/")),
        ("GET /health", lambda c, _: _time_request(c, "GET", f"{base}/health")),
        ("GET /health/llm", lambda c, _: _time_request(c, "GET", f"{base}/health/llm")),
        (
            "POST /tools/invoke",
            lambda c, _: _time_request(
                c,
                "POST",
                f"{base}/tools/invoke",
                json={"tool": "fetch_slots", "arguments": {"date": "2026-10-01"}},
            ),
        ),
        (
            "POST /stt",
            lambda c, _: _time_request(
                c,
                "POST",
                f"{base}/stt",
                files={"audio": ("bench.wav", wav, "audio/wav")},
            ),
        ),
        (
            "POST /tts",
            lambda c, _: _time_request(
                c,
                "POST",
                f"{base}/tts",
                json={"text": "Benchmark voice synthesis. One short sentence."},
            ),
        ),
        (
            "POST /agent/turn",
            lambda c, r: _time_request(
                c,
                "POST",
                f"{base}/agent/turn",
                json={
                    "message": "Reply with one short friendly sentence only.",
                    "session_id": f"bench-agent-r{r}-{time.time_ns()}",
                },
            ),
        ),
        (
            "POST /process",
            lambda c, r: _time_request(
                c,
                "POST",
                f"{base}/process",
                json={
                    "message": "Say hi in one sentence.",
                    "session_id": f"bench-process-r{r}-{time.time_ns()}",
                    "return_speech": False,
                },
            ),
        ),
        (
            "POST /conversation (text)",
            lambda c, r: _time_request(
                c,
                "POST",
                f"{base}/conversation",
                data={
                    "message": "Acknowledge in one sentence.",
                    "session_id": f"bench-conv-r{r}-{time.time_ns()}",
                    "return_speech": "false",
                },
            ),
        ),
        (
            "POST /agent/summary",
            lambda c, r: _timing_agent_summary(c, base, r),
        ),
    ]

    print("Voice healthcare agent — API performance (real network, production-style paths)")
    gate_ms = args.fail_if_any_route_mean_ms_above
    print(f"Base: {base}  |  rounds={args.rounds}  |  timeout={args.timeout}s  |  mean gate={gate_ms if gate_ms > 0 else 'off'}\n")

    gate_rows: list[tuple[str, float, bool]] = []

    failed = False
    with httpx.Client(timeout=args.timeout) as client:
        for name, fn in sequential:
            codes: list[int] = []
            times: list[float] = []
            for r in range(args.rounds):
                code, ms = fn(client, r)
                codes.append(code)
                times.append(ms)
            ok = all(c == 200 for c in codes)
            if not ok:
                failed = True
            mean = statistics.mean(times)
            row = (
                f"{name:<32}  HTTP {codes[0] if len(set(codes))==1 else codes}  "
                f"{mean:8.1f} ms mean   (min {min(times):.1f} / max {max(times):.1f})"
                + ("" if ok else "  <-- non-200")
            )
            print(row)
            if gate_ms > 0.0:
                gate_rows.append((name, mean, ok))

        au_code, au_ms = _time_request(
            client,
            "POST",
            f"{base}/conversation",
            files={"audio": ("bench.wav", wav, "audio/wav")},
            data={
                "session_id": f"bench-audio-{time.time_ns()}",
                "return_speech": "false",
            },
        )
        if au_code != 200:
            failed = True
        print(
            f"{'POST /conversation (audio)':<32}  HTTP {au_code}  {au_ms:8.1f} ms  (one shot)"
            + ("" if au_code == 200 else "  <-- non-200"),
        )
        if gate_ms > 0.0:
            gate_rows.append(("POST /conversation (audio)", au_ms, au_code == 200))

        if args.concurrent > 0:
            print(f"\nConcurrency: {args.concurrent} parallel GET /health × 3 waves")
            wave_ms: list[float] = []
            for _wave in range(3):
                t0 = time.perf_counter()
                with ThreadPoolExecutor(max_workers=args.concurrent) as ex:
                    futs = [
                        ex.submit(lambda: _time_request(client, "GET", f"{base}/health"))
                        for _ in range(args.concurrent)
                    ]
                    for f in as_completed(futs):
                        code, _ = f.result()
                        if code != 200:
                            failed = True
                wave_ms.append((time.perf_counter() - t0) * 1000.0)
            print(
                f"  Wall time per wave: {statistics.mean(wave_ms):.1f} ms mean "
                f"(min {min(wave_ms):.1f} / max {max(wave_ms):.1f}) "
                f"→ ~{args.concurrent * 1000 / statistics.mean(wave_ms):.0f} req/s (health only, rough)"
            )

    print("\nNotes:")
    print("  • First /stt round often includes Whisper load — treat max(stt) as cold-ish upper bound.")
    print("  • /agent/turn and /process include 2× Ollama calls (planner + finalizer); latency scales with model.")
    print("  • POST /agent/summary is timed alone (after a seed /agent/turn in the same helper).")
    print("  • For GPU efficiency, watch nvidia-smi during /stt and while Ollama serves /agent/turn.")
    print("  • Tune OLLAMA_NUM_PARALLEL on the ollama serve process for concurrent LLM throughput.\n")

    if gate_ms > 0.0:
        for nm, mean, ok_row in gate_rows:
            if not ok_row:
                continue
            if mean > gate_ms:
                print(f"Mean latency gate FAILED: {nm} mean {mean:.1f} ms > {gate_ms} ms")
                failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
