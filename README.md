# Voice Healthcare Agent

**Author:** MD Mutasim Billah Noman  

Monorepo: **FastAPI** backend (SQLite appointments, Ollama agent, faster-whisper STT, Piper TTS) and **Next.js 14** call UI at [`/call`](frontend/app/call/page.tsx). Optional **LiveKit** WebRTC uses the same pipeline as WebSocket audio.

### Cost-free / open-source stack (feature parity vs cloud vendors)

This project intentionally uses **local OSS** instead of paid APIs. You get the same product features (voice in/out, tools, summary, optional real-time room) without per-minute STT/TTS/LLM billing:

| Typical cloud component | Local replacement in this repo |
|-------------------------|----------------------------------|
| Managed STT (e.g. Deepgram) | **faster-whisper** (`WHISPER_*` in `.env`) |
| Managed TTS (e.g. Cartesia) | **Piper** (`PIPER_*`) |
| Hosted LLM API | **Ollama** (`OLLAMA_*`) — pull an instruct model once |
| Hosted Postgres | **SQLite** (`DATABASE_PATH`) |
| Real-time voice infra | **LiveKit** OSS server + `livekit_agent_worker.py` (optional) |
| Video / talking-head avatar SaaS | **Browser Web Audio** avatar on `/call` (level + mouth motion from audio — no third-party avatar SDK) |

For demos and take-homes, run everything on one machine (or GPU for Whisper). Scale-out and hardening are separate production concerns.

## Prerequisites

- Python **3.11+**
- Node **18.17+** (Next 14)
- **Docker** only if you use [LiveKit](docker-compose.livekit.yml)

## Quick start

**Backend** (from `backend/`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set PIPER_BINARY, PIPER_VOICE, OLLAMA_MODEL; for GPU STT without system libcublas.12, see Configuration / requirements-whisper-gpu.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Ollama** (separate terminal; or use [`scripts/run_with_tools.sh`](scripts/run_with_tools.sh) if tools live under `.tools/ollama/`):

```bash
ollama serve
ollama pull qwen2.5:7b-instruct
```

**Frontend** (from `frontend/`):

```bash
npm install
cp .env.local.example .env.local
npm run dev
```

If **`next dev`** fails with **`Cannot find module './NNN.js'`** (stale build cache), run **`npm run dev:fresh`** or **`npm run clean && npm run dev`**.

Open [http://localhost:3000/call](http://localhost:3000/call). Health check: [http://localhost:8000/docs](http://localhost:8000/docs).

## Database (SQLite)

The API uses a single **SQLite** file for appointments and (optionally) chat history.

| Topic | Detail |
|--------|--------|
| **Path** | Set **`DATABASE_PATH`** in `backend/.env` (see [`.env.example`](backend/.env.example)). Default: `data/appointments.db`. If the value is relative, it is resolved against the **process working directory** when the server starts—run **`uvicorn` from `backend/`** so the file lands under `backend/data/`, or use an **absolute** path in production. |
| **Initialization** | On startup the app opens the file, creates parent directories if needed, and runs **`CREATE TABLE IF NOT EXISTS`** for all tables ([schema](backend/app/db/database.py)). No separate migration step. |
| **Tables** | **`appointments`** — booked/cancelled slots (`UNIQUE(date, time)`). **`conversation_messages`** — per-`session_id` user/assistant rows when persistence is enabled. |
| **Conversation history** | With **`CONVERSATION_PERSIST=1`**, turns are written to **`conversation_messages`** so summaries survive API restarts. Without it, only appointments (and tool side effects) need the DB. |
| **Reset** | Stop the API, delete or replace the SQLite file (or point `DATABASE_PATH` at a new path), then restart—tables are recreated on boot. Back up the file if you need to keep data. |

Dev-only browser dump: **`ENABLE_DB_INSPECT=1`** and [`GET /internal/db/snapshot`](#api-overview) (see API overview below).

## Configuration

Full variable list: [`backend/.env.example`](backend/.env.example), [`frontend/.env.local.example`](frontend/.env.local.example).

| Area | Notes |
|------|--------|
| **CORS** | `CORS_ORIGINS` must include your frontend origin in production. |
| **Transcripts** | `CONVERSATION_PERSIST=1` stores dialogue in SQLite so `POST /agent/summary` works across API restarts. |
| **Phone locale** | `PHONE_DEFAULT_CC` (optional): set **`880`** (or `bd`) so Bangladesh national mobiles **01[3-9]…** (11 digits, e.g. **017…**) normalize to **+880**; unset defaults to inferring **UK** **07…** → **+44**. |
| **Deploy** | Backend: `uvicorn` from `backend/` with persistent `DATABASE_PATH`. Frontend: `NEXT_PUBLIC_API_URL` → public API. |
| **STT / GPU** | `WHISPER_DEVICE=auto` (default) or `cuda` uses the GPU when CTranslate2 sees CUDA. On startup the API prepends **`LD_LIBRARY_PATH`** with: `CUDA_LIBRARY_PATH`; pip **`nvidia/*/lib`** (e.g. after `pip install -r requirements-whisper-gpu.txt`); Conda `CONDA_PREFIX/lib`; `CUDA_HOME` and `/usr/local/cuda` targets; `/usr/lib/x86_64-linux-gnu`. If transcription still fails to load CUDA libs, set `CUDA_LIBRARY_PATH` to the directory containing `libcublas.so.12`, or use `WHISPER_DEVICE=cpu`. |

Vendor installs for Ollama/Piper under **`.tools/`** (optional): see comments in `.env.example`. Optional GPU STT CUDA 12 BLAS wheels: [`backend/requirements-whisper-gpu.txt`](backend/requirements-whisper-gpu.txt).

## Tests

```bash
source backend/.venv/bin/activate
cd backend && PYTHONPATH=. python -m pytest tests/ -q && bash scripts/qa_scenario_matrix.sh
```

With **API on :8000**, optional:

```bash
cd backend && bash scripts/e2e_real_smoke.sh    # lenient
cd backend && bash scripts/e2e_integration_real.sh   # strict
```

After a full local stack check, refresh [`backend/reports/latest-validation.txt`](backend/reports/latest-validation.txt) (see also `benchmark-output.txt` from `scripts/benchmark_api_performance.py`).

## Useful scripts (`backend/scripts/`)

| Script | Purpose |
|--------|---------|
| `e2e_integration_real.sh` | Strict smoke: needs Ollama + Piper + Whisper |
| `e2e_real_smoke.sh` | Lenient smoke |
| `benchmark_api_performance.py` | Route timings |
| `livekit_agent_worker.py` | LiveKit → same STT/agent path as `/ws/conversation_audio` |
| `qa_scenario_matrix.sh` | Fast pytest subset + optional `RUN_HTTP=1` |

## LiveKit (optional)

```bash
docker compose -f docker-compose.livekit.yml up -d
```

Set `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` in `backend/.env` (match server logs in dev). Install `requirements-livekit.txt`. Frontend: `NEXT_PUBLIC_LIVEKIT_URL` (e.g. `ws://127.0.0.1:7880`). Run `PYTHONPATH=. python scripts/livekit_agent_worker.py` from `backend/` with `LIVEKIT_URL` / `LIVEKIT_ROOM` matching the UI room name.

`livekit_worker_stub.py` is connect-only; use `livekit_agent_worker.py` for the real pipeline.

## API overview

Interactive docs: **`GET /docs`**. Common routes: `POST /process`, `POST /conversation`, `POST /agent/summary`, `POST /tools/invoke`, `POST /stt`, `POST /tts`. WebSockets: `/ws/agent`, `/ws/conversation_audio`.

**Inspect SQLite in the browser (dev only):** set **`ENABLE_DB_INSPECT=1`** in `backend/.env`, restart uvicorn, then open  
[http://127.0.0.1:8000/internal/db/snapshot](http://127.0.0.1:8000/internal/db/snapshot)  
Optional query params: `appointments_limit`, `messages_limit`, `session_id` (filter messages). Without the env flag this URL returns **404**.

Default ports: API **8000**, Next **3000**, Ollama **11434**, LiveKit signal **7880**.

## Future improvements

- **Incremental streaming ASR (design, non-breaking):** Keep today’s **finalize-then-transcribe** path as the canonical contract for `/ws/conversation_audio` and LiveKit (bounded buffer → one `transcribe()` → agent). Add optional phases without removing it:
  1. **Endpointing on the client:** VAD / silence detection to cap clip length and send shorter finalizes (same server code, lower latency).
  2. **Optional interim channel:** Same WebSocket or a parallel topic delivering partial transcripts for UI only; **agent turns still wait** for a finalized segment or explicit end-of-utterance to avoid double tool calls.
  3. **Server streaming decode:** If faster-whisper (or a secondary engine) exposes streaming APIs, buffer segments and merge before planner input while preserving the existing JSON event stream for tools.
- Hardened production LiveKit (`wss://`, autoscaling workers).
