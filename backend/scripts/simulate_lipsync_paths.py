#!/usr/bin/env python3
"""
Debug lipsync like the /call UI: fetch assistant WAV (REST or WebSocket audio), then POST /avatar/lipsync.

**Modes**
- ``rest`` — ``POST /process`` with ``return_speech`` (same as text box + spoken reply).
- ``ws-audio`` — ``/ws/conversation_audio`` start → binary WAV → finalize (same as push-to-talk WebSocket).
- ``livekit-va-example`` — print sample ``va`` JSON lines (segmented ``tts_begin``) for comparing with DevTools / worker logs.

Requires a running API (uvicorn). For ``ws-audio``, ``pip install websockets`` (usually already installed via uvicorn/livekit).

Examples::

  cd backend && . .venv/bin/activate
  python scripts/simulate_lipsync_paths.py --mode rest --message "Say hello in one short sentence."
  python scripts/simulate_lipsync_paths.py --mode ws-audio --wav /path/to/short.wav
  python scripts/simulate_lipsync_paths.py --mode livekit-va-example
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import wave
from pathlib import Path


def _http_to_ws_url(api_base: str) -> str:
    u = api_base.strip().rstrip("/")
    if u.startswith("https://"):
        return "wss://" + u[len("https://") :]
    if u.startswith("http://"):
        return "ws://" + u[len("http://") :]
    return u


def _default_silence_wav() -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"\x00\x00" * 800)
    return buf.getvalue()


def _run_rest(api_base: str, message: str, session_id: str, skip_lipsync: bool) -> int:
    import httpx

    t0 = time.perf_counter()
    r = httpx.post(
        f"{api_base.rstrip('/')}/process",
        json={"message": message, "session_id": session_id, "return_speech": True},
        timeout=600.0,
    )
    print(f"process status={r.status_code} elapsed_ms={int((time.perf_counter() - t0) * 1000)}")
    if r.status_code >= 400:
        print(r.text[:500])
        return 1
    data = r.json()
    b64 = data.get("audio_wav_base64")
    if not isinstance(b64, str) or not b64.strip():
        print("No audio_wav_base64 (return_speech off or TTS unavailable).")
        return 0
    wav = base64.b64decode(b64)
    print(f"wav_bytes={len(wav)} final_response={str(data.get('final_response') or '')[:120]!r}")
    if skip_lipsync:
        return 0
    t1 = time.perf_counter()
    lr = httpx.post(
        f"{api_base.rstrip('/')}/avatar/lipsync",
        files={"audio": ("ui_chain.wav", wav, "audio/wav")},
        timeout=600.0,
    )
    print(
        f"lipsync status={lr.status_code} elapsed_ms={int((time.perf_counter() - t1) * 1000)} "
        f"body_bytes={len(lr.content) if lr.is_success else 0}",
    )
    if not lr.is_success:
        print(lr.text[:400])
    return 0 if lr.is_success else 1


def _run_ws_audio(api_base: str, wav_path: Path | None, session_id: str, skip_lipsync: bool) -> int:
    try:
        from websockets.sync.client import connect
    except ImportError:
        print("Install websockets: pip install websockets", file=sys.stderr)
        return 2

    ws_url = _http_to_ws_url(api_base) + "/ws/conversation_audio"
    if wav_path is not None:
        audio = wav_path.read_bytes()
    else:
        audio = _default_silence_wav()

    t0 = time.perf_counter()
    with connect(ws_url, open_timeout=30) as ws:
        ws.send(
            json.dumps(
                {
                    "action": "start",
                    "session_id": session_id,
                    "return_speech": True,
                    "file_extension": ".wav",
                },
            ),
        )
        ready_raw = ws.recv()
        ready = json.loads(ready_raw) if isinstance(ready_raw, str) else json.loads(ready_raw.decode())
        if ready.get("type") != "ready":
            print(f"unexpected first message: {ready}")
            return 1
        ws.send(audio)
        ws.send(json.dumps({"action": "finalize"}))
        t_after_finalize = time.perf_counter()

        done: dict | None = None
        t_stt: float | None = None
        for _ in range(256):
            raw = ws.recv()
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "stt":
                t_stt = time.perf_counter()
            if mtype == "done":
                done = msg
                t_done = time.perf_counter()
                print(
                    f"ws_timing after_finalize_to_done_ms={int((t_done - t_after_finalize) * 1000)} "
                    f"total_ws_ms={int((t_done - t0) * 1000)}"
                    + (
                        f" stt_to_done_ms={int((t_done - t_stt) * 1000)}"
                        if t_stt is not None
                        else ""
                    ),
                )
                break
            if mtype == "error":
                print(f"ws error: {msg}")
                return 1
        else:
            print("Timeout: no done event after 256 ws messages.")
            return 1

    if not done:
        return 1
    b64 = done.get("audio_wav_base64")
    if not isinstance(b64, str) or not b64.strip():
        print("done without audio_wav_base64 (check Piper / return_speech).")
        print(json.dumps({k: v for k, v in done.items() if k != "audio_wav_base64"}, indent=2)[:2000])
        return 0
    wav = base64.b64decode(b64)
    print(f"wav_bytes={len(wav)}")
    if skip_lipsync:
        return 0

    import httpx

    t1 = time.perf_counter()
    lr = httpx.post(
        f"{api_base.rstrip('/')}/avatar/lipsync",
        files={"audio": ("ui_chain.wav", wav, "audio/wav")},
        timeout=600.0,
    )
    print(
        f"lipsync status={lr.status_code} elapsed_ms={int((time.perf_counter() - t1) * 1000)} "
        f"body_bytes={len(lr.content) if lr.is_success else 0}",
    )
    if not lr.is_success:
        print(lr.text[:400])
    return 0 if lr.is_success else 1


def _print_livekit_va_example() -> int:
    utterance_id = "dryrun_utts_01"
    n = 2
    print(
        "// Then: tts_wav_chunk + lipsync_mp4_chunk share `rid`; "
        "UI sync: utterance anchor + audio_offset_ms.",
        file=sys.stderr,
    )
    for i in range(n):
        off = 0.0 if i == 0 else 750.0 * i
        rid = f"{utterance_id}_{i}"[:128]
        line = {
            "kind": "tts_begin",
            "utterance_id": utterance_id,
            "segment_index": i,
            "segment_count": n,
            "audio_offset_ms": round(off, 2),
            "rid": rid,
            "worker_lipsync": True,
        }
        print(json.dumps(line, sort_keys=True))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Simulate UI lipsync chains (REST / WebSocket / LiveKit VA reference).")
    p.add_argument("--api-base", default="http://127.0.0.1:8000", help="FastAPI base URL")
    p.add_argument("--mode", choices=("rest", "ws-audio", "livekit-va-example"), default="rest")
    p.add_argument("--message", default="Hello — reply in one short sentence.", help="rest: user message")
    p.add_argument("--session-id", default="debug-lipsync", help="session_id for REST/WS")
    p.add_argument("--wav", type=Path, default=None, help="ws-audio: optional WAV file (default: tiny silence)")
    p.add_argument("--skip-lipsync", action="store_true", help="only fetch WAV / events; no POST /avatar/lipsync")
    args = p.parse_args()

    if args.mode == "rest":
        return _run_rest(args.api_base, args.message, args.session_id, args.skip_lipsync)
    if args.mode == "ws-audio":
        return _run_ws_audio(args.api_base, args.wav, args.session_id, args.skip_lipsync)
    return _print_livekit_va_example()


if __name__ == "__main__":
    raise SystemExit(main())
