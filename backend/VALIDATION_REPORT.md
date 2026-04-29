# Validation baseline

Snapshot captured after **`scripts/e2e_integration_real.sh`** and **`scripts/benchmark_api_performance.py`** on a warm stack (**API:** `127.0.0.1:8000`, Ollama, Piper, faster-whisper). Re-run when hardware or deps change.

**Operational setup (verified 2026-04-30):** Local LiveKit Docker — keys from **`docker compose … logs`** into `backend/.env`; `pip install -r requirements-livekit.txt`; **`GET /livekit/token`** returns **200** + JWT when **`LIVEKIT_API_KEY`** / **`LIVEKIT_API_SECRET`** are set. Frontend **`NEXT_PUBLIC_LIVEKIT_URL`** in `.env.local` must match signaling.

Roadmap gaps (streaming ASR, multi-process sessions, prod LiveKit ops) → **README**.

## API checklist

| Scenario | Expected | Notes |
|----------|-----------|-------|
| `GET /health` | 200 | |
| `GET /health/llm` | 200 | Requires `ollama serve` |
| `GET /livekit/token` | 200 | `LIVEKIT_*` + livekit-api |
| `POST /tools/invoke` `fetch_slots` | 200 | SQLite tools |
| `POST /stt` (wav) | 200 | Whisper |
| `POST /tts` | 200 | Piper |
| `POST /agent/turn` | 200 | Ollama planner + finalize |
| `POST /process` `return_speech:false` | 200 | Text agent |
| `POST /conversation` text + TTS | 200 | Piper in pipeline |
| `POST /agent/summary` (after `/conversation`) | 200 | Session `e2e-conv` in script |

Matrix test (SQLite tools, no LLM): **`tests/test_qa_matrix_db.py`** — book/modify/double-book/cancel/retrieve.

---

## Benchmark (2026-04-30)

`benchmark_api_performance.py --rounds 5 --concurrent 16`.

| Route | Mean (ms) | Min–Max (ms) |
|-------|-----------|--------------|
| `GET /` | ~1.3 | 0.9–2.8 |
| `GET /health` | ~0.9 | 0.8–0.9 |
| `GET /health/llm` | ~20 | 18–26 |
| `POST /tools/invoke` | ~1.2 | 1.2–1.4 |
| `POST /stt` | ~1094 | 1085–1116 |
| `POST /tts` | ~502 | 441–634 |
| `POST /agent/turn` | ~1631 | 1528–1836 |
| `POST /process` | ~1338 | 1329–1359 |
| `POST /conversation` (text) | ~1883 | 1605–2278 |
| `POST /agent/summary` | ~563 | 475–611 |
| `POST /conversation` (audio) | ~1117 | one-shot |

Bottleneck summary: conversational routes are dominated by **two serial LLM calls**; **`/stt`** by Whisper; **`/tts`** by Piper. Optional SLA gate: **`--fail-if-any-route-mean-ms-above`** on the benchmark script.
