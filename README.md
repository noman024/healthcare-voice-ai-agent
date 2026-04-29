# Voice Healthcare Agent

**Author:** MD Mutasim Billah Noman

Monorepo: **FastAPI** backend (SQLite, Ollama, faster-whisper, Piper) and **Next.js 14** UI at `[/call](frontend/app/call/page.tsx)`.

## Architecture


| Piece                          | Responsibility                                                                                                                                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **FastAPI** (`backend/`)       | HTTP API, `POST /stt`, `POST /tts`, `POST /tools/invoke`, WebSockets (`/ws/agent`, `/ws/conversation_audio`), `GET /livekit/token`, SQLite                                                             |
| **Ollama**                     | LLM inference (OpenAI-compatible `/v1` used by the API and by the LiveKit worker)                                                                                                                      |
| **Browser — main path**        | `/call` can use REST + **WebSocket** voice; STT/TTS/agent run **inside FastAPI**                                                                                                                       |
| **Browser — optional LiveKit** | WebRTC mic → **livekit-agents** worker: VAD, batch STT (same faster-whisper stack as `/stt`), LLM (Ollama), TTS via `**POST {VOICE_API_BASE}/tts`** (Piper), tools via **same SQLite file** as the API |


LiveKit does **not** replace FastAPI: the worker calls the API for TTS and shares the DB file for tool execution.

### Cost-free stack (vs typical cloud)


| Cloud-style               | This repo                                                                                         |
| ------------------------- | ------------------------------------------------------------------------------------------------- |
| Managed STT               | **faster-whisper** (`WHISPER_`* in `backend/.env`)                                                |
| Managed TTS               | **Piper** (`PIPER_`*)                                                                             |
| Hosted LLM                | **Ollama** (`OLLAMA_`*)                                                                           |
| Database                  | **SQLite** (`DATABASE_PATH`)                                                                      |
| Real-time room (optional) | **LiveKit** server + `[backend/scripts/run_voice_worker.py](backend/scripts/run_voice_worker.py)` |
| Avatar                    | In-browser level/mouth from audio (no third-party avatar SDK)                                     |


## Prerequisites

- Python **3.11+**
- Node **18.17+**
- **Docker** only if you use LiveKit via `[docker-compose.livekit.yml](docker-compose.livekit.yml)`

Always use the **backend virtual environment** for Python: `source backend/.venv/bin/activate` (after creating it below).

## Quick start (local)

### 1. Backend API

From the **repository root**:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` at minimum: `**PIPER_BINARY**`, `**PIPER_VOICE**`, `**OLLAMA_MODEL**` (see comments in `[.env.example](backend/.env.example)`). For GPU STT without system CUDA BLAS, see `[backend/requirements-whisper-gpu.txt](backend/requirements-whisper-gpu.txt)`.

With venv **still active** and cwd `**backend/`**:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. Ollama

Separate terminal:

```bash
ollama serve
ollama pull qwen2.5:7b-instruct
```

(Or use `[scripts/run_with_tools.sh](scripts/run_with_tools.sh)` if tools live under `.tools/ollama/`.)

### 3. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Set `**NEXT_PUBLIC_API_URL**` in `.env.local` to your API (default `http://localhost:8000`).

### 4. Open the app

- UI: [http://localhost:3000/call](http://localhost:3000/call)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

If `**next dev**` errors with `**Cannot find module './NNN.js'**`, run `**npm run dev:fresh**` or `**npm run clean && npm run dev**`.

## LiveKit voice (optional)

Use this when you want **browser WebRTC** + **livekit-agents** instead of (or alongside) the WebSocket voice path on `/call`.

**Order matters:** LiveKit server → `backend/.env` → install worker deps → start **FastAPI** → start **worker** → connect from the UI.

1. **Signal server** (from repo root):
  ```bash
   docker compose -f docker-compose.livekit.yml up -d
  ```
   In dev, logs usually print `**LIVEKIT_API_KEY**` / `**LIVEKIT_API_SECRET**`. Copy them into `**backend/.env**` (never commit secrets).
2. **Backend env** (`backend/.env`):
  - `LIVEKIT_URL` — e.g. `ws://127.0.0.1:7880` (must match the server).
  - `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — must match the server.
  - `VOICE_API_BASE` — URL of **this** FastAPI app as the worker will call it (default `http://127.0.0.1:8000`). Change if the API listens elsewhere.
  - The worker reuses `**OLLAMA_*`** / `**OLLAMA_MODEL**` from the same file when API and worker run on one machine.
3. **Worker dependencies** (venv active, `cd backend/`):
  ```bash
   pip install -r requirements-livekit.txt
  ```
4. **Silero assets** (first run only, if prompted): from `backend/` with venv active, run the same CLI entrypoint as the worker:

   ```bash
   PYTHONPATH=. python scripts/run_voice_worker.py download-files
   ```

   (`python -m livekit.agents.cli` is not valid in livekit-agents 1.5.x; `download-files` is a subcommand on your worker’s Typer app.)
5. **Run processes** (three terminals, venv active for Python):
  - Terminal A: `uvicorn` in `backend/` (as above).
  - Terminal B: from `backend/`:
    ```bash
    source .venv/bin/activate
    PYTHONPATH=. python scripts/run_voice_worker.py
    ```
  - Terminal C: `npm run dev` in `frontend/`.
6. **Frontend env** (`frontend/.env.local`):
  - `NEXT_PUBLIC_LIVEKIT_URL` — e.g. `ws://127.0.0.1:7880`.
  - `NEXT_PUBLIC_LIVEKIT_DEFAULT_ROOM` — default room name shown on `/call`; use the **same** name when you click **Connect** in the LiveKit panel so the browser and agent share one room.

## Database (SQLite)


| Topic      | Detail                                                                                                                                            |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Path**   | `**DATABASE_PATH`** in `backend/.env`. Relative paths resolve from the **process cwd**—run `**uvicorn` from `backend/`** or use an absolute path. |
| **Init**   | `CREATE TABLE IF NOT EXISTS` on startup (`[backend/app/db/database.py](backend/app/db/database.py)`).                                             |
| **Tables** | `appointments`; optional `conversation_messages` when `**CONVERSATION_PERSIST=1`**.                                                               |
| **Reset**  | Stop API, delete/replace the DB file, restart.                                                                                                    |


Dev-only: `**ENABLE_DB_INSPECT=1`** enables `[GET /internal/db/snapshot](http://127.0.0.1:8000/internal/db/snapshot)`.

## Configuration reference

Templates: `[backend/.env.example](backend/.env.example)`, `[frontend/.env.local.example](frontend/.env.local.example)`, repo root `[.env.example](.env.example)`.


| Area             | Notes                                                                                                                                                       |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CORS**         | `CORS_ORIGINS` must include the Next.js origin in production.                                                                                               |
| **Transcripts**  | `CONVERSATION_PERSIST=1` keeps dialogue in SQLite for `POST /agent/summary` across restarts.                                                                |
| **Phone locale** | Optional `PHONE_DEFAULT_CC` (e.g. `**880`** / `bd` vs UK `**07…**` → `**+44**`).                                                                            |
| **STT / GPU**    | `WHISPER_DEVICE`, `CUDA_LIBRARY_PATH`, etc.—see `.env.example` and README notes in `[requirements-whisper-gpu.txt](backend/requirements-whisper-gpu.txt)`.  |
| **LiveKit**      | Backend: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `VOICE_API_BASE`. Frontend: `NEXT_PUBLIC_LIVEKIT_URL`, `NEXT_PUBLIC_LIVEKIT_DEFAULT_ROOM`. |


Optional vendor paths under `**.tools/`** are documented in `backend/.env.example`.

## Tests

```bash
source backend/.venv/bin/activate
cd backend
PYTHONPATH=. python -m pytest tests/ -q
bash scripts/qa_scenario_matrix.sh
```

With API on **:8000**, optional integration scripts (may need Ollama + Piper + Whisper):

```bash
source backend/.venv/bin/activate
cd backend
bash scripts/e2e_real_smoke.sh
bash scripts/e2e_integration_real.sh
```

## Useful scripts (`backend/scripts/`)


| Script                         | Purpose                                                      |
| ------------------------------ | ------------------------------------------------------------ |
| `run_voice_worker.py`          | LiveKit Agents entrypoint (after `requirements-livekit.txt`) |
| `e2e_integration_real.sh`      | Strict stack smoke                                           |
| `e2e_real_smoke.sh`            | Lenient smoke                                                |
| `benchmark_api_performance.py` | Route timings                                                |
| `qa_scenario_matrix.sh`        | Pytest subset + optional `RUN_HTTP=1`                        |


## API overview

- **HTTP:** `POST /process`, `POST /conversation`, `POST /agent/summary`, `POST /tools/invoke`, `POST /stt`, `POST /tts`, `GET /livekit/token`
- **WebSocket:** `/ws/agent`, `/ws/conversation_audio`
- **Docs:** `GET /docs`

Default ports: API **8000**, Next **3000**, Ollama **11434**, LiveKit **7880**.

## Future improvements

- **WebSocket ASR:** keep finalize-then-transcribe for `/ws/conversation_audio`; optional client endpointing, interim UI channel, or server streaming decode without changing tool semantics.
- **LiveKit in production:** `wss://`, hardened keys, worker scaling.

