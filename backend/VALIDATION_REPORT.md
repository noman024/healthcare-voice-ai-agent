# Validation baseline

Snapshot captured after **`scripts/e2e_integration_real.sh`** and **`scripts/benchmark_api_performance.py`** on a warm stack (**API:** `127.0.0.1:8000`, Ollama, Piper, faster-whisper). Re-run when hardware or deps change.

**Operational setup (verified 2026-04-30):** Local LiveKit Docker — keys from **`docker compose … logs`** into `backend/.env`; `pip install -r requirements-livekit.txt`; **`GET /livekit/token`** returns **200** + JWT when **`LIVEKIT_API_KEY`** / **`LIVEKIT_API_SECRET`** are set. Frontend **`NEXT_PUBLIC_LIVEKIT_URL`** in `.env.local` must match signaling.

Roadmap gaps (streaming ASR, multi-process sessions, prod LiveKit ops) → **README**, incremental STT design → **[docs/STREAMING_STT_ROADMAP.md](docs/STREAMING_STT_ROADMAP.md)**.

## Gate run (implementation verification)

| Check | Result |
|-------|--------|
| `pytest tests/` | **61 passed** |
| `scripts/e2e_integration_real.sh` | **OK** (requires live API + Ollama + Piper + Whisper on `:8000`) |
| Benchmark | See table below (`--rounds 5 --concurrent 16`) |

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
| `GET /` | 1.2 | 0.8–2.5 |
| `GET /health` | 0.8 | 0.8–0.8 |
| `GET /health/llm` | 19.2 | 18.7–19.6 |
| `POST /tools/invoke` | 1.3 | 1.2–1.5 |
| `POST /stt` | 1107.3 | 1098.7–1124.8 |
| `POST /tts` | 579.1 | 463.9–715.1 |
| `POST /agent/turn` | 1407.9 | 1042.3–2101.5 |
| `POST /process` | 1651.1 | 1346.1–1821.5 |
| `POST /conversation` (text) | 1597.0 | 1284.2–1799.7 |
| `POST /agent/summary` | 485.1 | 410.2–576.9 |
| `POST /conversation` (audio) | 1136.4 | one-shot |

Bottleneck summary: conversational routes are dominated by **two serial LLM calls**; **`/stt`** by Whisper; **`/tts`** by Piper. Optional SLA gate: **`--fail-if-any-route-mean-ms-above`** on the benchmark script.

### Latency tuning (dual LLM)

Optional env vars (defaults preserve backward compatibility — both passes use **`OLLAMA_MODEL`**):

| Variable | Purpose |
|----------|---------|
| `OLLAMA_PLANNER_MODEL` | Tag for structured JSON planner (`response_format=json`). Example: smaller instruct model for faster tool selection. |
| `OLLAMA_FINALIZE_MODEL` | Tag for natural-language reply after tool execution. |

Startup warmup primes each distinct configured tag when **`WARMUP_MODELS=1`**.
