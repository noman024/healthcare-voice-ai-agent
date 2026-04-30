# Voice Healthcare Agent

**Author:** MD Mutasim Billah Noman

Monorepo: **FastAPI** backend (SQLite, Ollama, faster-whisper, Piper) and **Next.js 14** UI at `[/call](frontend/app/call/page.tsx)`.

## Architecture


| Piece                          | Responsibility                                                                                                                                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **FastAPI** (`backend/`)       | HTTP API, `POST /stt`, `POST /tts`, `POST /tools/invoke`, WebSockets (`/ws/agent`, `/ws/conversation_audio`), `GET /livekit/token`, SQLite                                                             |
| **Ollama**                     | LLM inference (OpenAI-compatible `/v1` used by the API and by the LiveKit worker)                                                                                                                      |
| **Browser — main path**        | `/call` can use REST + **WebSocket** voice; STT/TTS/agent run **inside FastAPI**                                                                                                                       |
| **Browser — optional LiveKit** | WebRTC mic → **livekit-agents** worker: VAD, batch STT (same faster-whisper stack as `/stt`), LLM (Ollama), TTS via `POST {VOICE_API_BASE}/tts` (Piper), tools via **same SQLite file** as the API |


LiveKit does **not** replace FastAPI: the worker calls the API for TTS and shares the DB file for tool execution.

### Cost-free stack (vs typical cloud)


| Cloud-style               | This repo                                                                                         |
| ------------------------- | ------------------------------------------------------------------------------------------------- |
| Managed STT               | **faster-whisper** (`WHISPER_`* in `backend/.env`)                                                |
| Managed TTS               | **Piper** (`PIPER_`*)                                                                             |
| Hosted LLM                | **Ollama** (`OLLAMA_`*)                                                                           |
| Database                  | **SQLite** (`DATABASE_PATH`)                                                                      |
| Real-time room (optional) | **LiveKit** server + `[backend/scripts/run_voice_worker.py](backend/scripts/run_voice_worker.py)` |
| Avatar                    | Optional **MuseTalk** (`/avatar/lipsync`) + in-browser level/mouth from audio                      |


### Enterprise / managed providers (not wired here)

Some specs reference **Deepgram** (STT), **Cartesia** (TTS), and **Tavus / Beyond Presence** (avatar). This repo uses **faster-whisper**, **Piper**, **Ollama**, and optional **MuseTalk** instead. Replacing providers means changing STT/TTS wiring in [`backend/app/lk_agents/voice_agent.py`](backend/app/lk_agents/voice_agent.py) and related FastAPI routes—not a single env toggle today.

## Repository layout

| Path | Role |
|------|------|
| [`backend/app/main.py`](backend/app/main.py) | FastAPI app factory, lifespan, CORS |
| [`backend/app/routers/`](backend/app/routers/) | HTTP and WebSocket routes (health, LiveKit, agent, audio, avatar, internal) |
| [`backend/app/agent/`](backend/app/agent/) | LLM planner/finalizer runner, memory, guards |
| [`backend/app/tools/`](backend/app/tools/) | SQLite-backed tool execution + validation |
| [`backend/app/conversation/`](backend/app/conversation/) | Text/audio pipelines and WebSocket batch finalize |
| [`backend/app/lk_agents/`](backend/app/lk_agents/) | LiveKit worker: `voice_agent.py`, STT/TTS adapters, publish/transcript helpers |
| [`backend/app/musetalk/`](backend/app/musetalk/) | Optional lip-sync API and inference bridge |
| [`backend/app/db/`](backend/app/db/) | SQLite schema and repositories |
| [`frontend/app/call/`](frontend/app/call/) | `/call` UI: [`page.tsx`](frontend/app/call/page.tsx), shared [`callUtils.ts`](frontend/app/call/callUtils.ts) / [`audioPlayback.ts`](frontend/app/call/audioPlayback.ts) |

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

Edit `backend/.env` at minimum: `PIPER_BINARY`, `PIPER_VOICE`, `OLLAMA_MODEL` (see comments in [`backend/.env.example`](backend/.env.example)). For GPU STT without system CUDA BLAS, see [`backend/requirements-whisper-gpu.txt`](backend/requirements-whisper-gpu.txt).

To run the Python test suite you also need dev dependencies (pytest is not in the default `requirements.txt`):

```bash
pip install -r requirements-dev.txt
```

From the same venv and `backend/` directory:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. Ollama

Separate terminal:

```bash
ollama serve
ollama pull qwen2.5:7b-instruct
```

(Or use [`scripts/run_with_tools.sh`](scripts/run_with_tools.sh) if tools live under `.tools/ollama/`.)

### 3. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Set `NEXT_PUBLIC_API_URL` in `.env.local` to your API (default `http://localhost:8000`).

### 4. Open the app

- UI: [http://localhost:3000/call](http://localhost:3000/call)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

If `next dev` errors with `Cannot find module './NNN.js'`, run `npm run dev:fresh` or `npm run clean && npm run dev`.

## LiveKit voice (optional)

Use this when you want **browser WebRTC** + **livekit-agents** instead of (or alongside) the WebSocket voice path on `/call`.

**Order matters:** LiveKit server → `backend/.env` → install worker deps → start **FastAPI** → start **worker** → connect from the UI.

1. **Signal server** (from repo root):
  ```bash
   docker compose -f docker-compose.livekit.yml up -d
  ```
   In dev, logs usually print `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`. Copy them into `backend/.env` (never commit secrets).
2. **Backend env** (`backend/.env`):
  - `LIVEKIT_URL` — e.g. `ws://127.0.0.1:7880` (must match the server).
  - `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — must match the server.
   - `VOICE_API_BASE` — URL of **this** FastAPI app as the worker will call it (default `http://127.0.0.1:8000`). Change if the API listens elsewhere.
   - `VOICE_INTERNAL_SECRET` — **same random string** in API + worker env so the worker can `POST /internal/voice/worker/transcript` after each user/assistant line. Required for **call summaries** on the LiveKit-only path (mirrors `/call` `conversation_id` into SQLite). If unset, the route returns 404 and the worker skips persistence.
  - The worker reuses `OLLAMA_*` / `OLLAMA_MODEL` from the same file when API and worker run on one machine.
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
   - Optional: `NEXT_PUBLIC_MUSETALK_ENABLED=1` when the API has MuseTalk enabled — see **MuseTalk lip-sync** below.

## MuseTalk lip-sync (optional, GPU)

End-to-end / production path for **video** lipsync after Piper TTS on routes that return `audio_wav_base64` (text chat, push-to-talk upload, WebSocket voice with `return_speech`). **LiveKit** can stream assistant audio only, or—with `VOICE_WORKER_LIPSYNC=1` (default) and `NEXT_PUBLIC_MUSETALK_ENABLED=1`—the worker POSTs WAV to `/avatar/lipsync` and pushes MP4 chunks to the browser over LiveKit data (see `voice_agent.py` and `LiveKitPanel.tsx`).

1. **Clone** into `third_party/MuseTalk` (folder is gitignored — create it beside `backend/`):

   ```bash
   mkdir -p third_party && git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git third_party/MuseTalk
   ```

2. **Fix image-reference cleanup** (upstream bug for static portraits):

   ```bash
   python backend/scripts/fix_musetalk_inference_image.py
   ```

3. **Weights** — idempotent download (needs `hf` from `huggingface_hub` and `gdown`; use your backend venv or `pip install huggingface_hub gdown`):

   ```bash
   export PATH="$PWD/backend/.venv/bin:$PATH"   # or wherever `hf` is installed
   bash backend/scripts/setup_musetalk_weights.sh
   ```

   Manual option: follow [MuseTalk README](https://github.com/TMElyralab/MuseTalk) / HuggingFace `TMElyralab/MuseTalk` so `models/musetalkV15/unet.pth`, `models/whisper/`, etc. exist under `MUSETALK_ROOT`.

4. **Python** — MuseTalk depends on OpenMMLab (`mmcv`, `mmpose`, …). Upstream assumes **Python 3.10** and a CUDA PyTorch build; plain **Python 3.12** venvs often fail on `chumpy` / old pins. Prefer **conda** (or Docker) with 3.10, then install upstream deps and point the API at that interpreter:

   ```bash
   conda create -n musetalk python=3.10 -y && conda activate musetalk
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
   cd third_party/MuseTalk && pip install -r requirements.txt
   # then openmim / mmcv / mmdet / mmpose per MuseTalk README
   ```

   In `backend/.env`: `MUSETALK_PYTHON=/absolute/path/to/that/bin/python`

   After `pip install -r requirements.txt`, **remove TensorFlow** (inference does not use it; on many Linux hosts it still gets imported indirectly and can raise **SIGILL** during `diffusers` / VAE import):

   ```bash
   pip uninstall -y tensorflow tensorboard tensorflow-estimator tensorflow-io-gcs-filesystem
   ```

   If `mim install "mmpose==1.1.0"` fails building **chumpy**, run `pip install chumpy --no-build-isolation` and retry.

5. **Reference portrait** — default `backend/assets/musetalk/reference.jpg` (replace with a clear front-facing face for your brand). Set `MUSETALK_REFERENCE_IMAGE` if needed.

6. **FFmpeg** — required to mux the MP4. Install `ffmpeg` on `PATH`, **or** run `bash backend/scripts/setup_ffmpeg_static.sh` and set `MUSETALK_FFMPEG_PATH=third_party/ffmpeg-static/current` in `backend/.env` (path is relative to the **repo root**).

7. **Dedicated port** — run MuseTalk on **8001** (or any port) so the main API stays on **8000**:

   ```bash
   cd backend && uvicorn app.musetalk.service_app:app --host 0.0.0.0 --port 8001
   ```

   In `backend/.env` on the machine that runs the **main** API, set `MUSETALK_SERVICE_URL=http://127.0.0.1:8001` so `/avatar/lipsync` and `/avatar/lipsync/status` are forwarded there. The MuseTalk process uses the same `backend/.env`; set `MUSETALK_ENABLED=1` there (and `MUSETALK_PYTHON`, weights paths, etc.). If you omit `MUSETALK_SERVICE_URL`, the main API runs inference in-process instead.

8. **Enable** — `MUSETALK_ENABLED=1` on the **MuseTalk service**, `NEXT_PUBLIC_MUSETALK_ENABLED=1`, and optionally `NEXT_PUBLIC_MUSETALK_API_URL=http://127.0.0.1:8001` so the browser talks to the lip-sync service directly (CORS is enabled on `service_app`). Check `GET http://localhost:8000/avatar/lipsync/status` (proxied) or `GET http://localhost:8001/avatar/lipsync/status` (direct) for `{ "ready": true, "ffmpeg": true }`.

9. **API** — `POST /avatar/lipsync` (multipart field `audio`, WAV) returns MP4. Inference is **single-flight** per GPU (`MUSETALK_SINGLE_FLIGHT`, default on).

## Database (SQLite)




| Topic      | Detail                                                                                                                                            |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Path**   | `DATABASE_PATH` in `backend/.env`. Relative paths resolve from the **process cwd**—run `uvicorn` from `backend/` or use an absolute path. |
| **Init**   | `CREATE TABLE IF NOT EXISTS` on startup (`[backend/app/db/database.py](backend/app/db/database.py)`).                                             |
| **Tables** | `appointments`; `conversation_messages` written by (**a**) `CONVERSATION_PERSIST=1` on REST/WebSocket turns, or (**b**) the LiveKit worker when `VOICE_INTERNAL_SECRET` is set. |
| **Reset**  | Stop API, delete/replace the DB file, restart.                                                                                                    |


Dev-only: `ENABLE_DB_INSPECT=1` enables [`GET /internal/db/snapshot`](http://127.0.0.1:8000/internal/db/snapshot).

## Configuration reference

Templates: `[backend/.env.example](backend/.env.example)` (authoritative for the API and worker), `[frontend/.env.local.example](frontend/.env.local.example)` (browser `NEXT_PUBLIC_*` vars), and repo root `[.env.example](.env.example)` (monorepo checklist only — not loaded by the apps).

`backend/.env.example` documents every variable the backend reads; optional or advanced keys appear commented. The frontend reads **only** `NEXT_PUBLIC_*` keys at build/start — see `frontend/.env.local.example` and grep `NEXT_PUBLIC_` under `frontend/` if you add new client flags.


| Area             | Notes                                                                                                                                                       |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CORS**         | `CORS_ORIGINS` must include the Next.js origin in production.                                                                                               |
| **Transcripts**  | `CONVERSATION_PERSIST=1` persists REST/WebSocket dialogue. **LiveKit:** set `VOICE_INTERNAL_SECRET` (API + worker) so turns also land in `conversation_messages` under the browser `conversation_id` for `POST /agent/summary`. |
| **Phone locale** | Optional `PHONE_DEFAULT_CC` (e.g. **880** / `bd` vs UK **07…** → **+44**). |
| **STT / GPU**    | `WHISPER_DEVICE`, `CUDA_LIBRARY_PATH`, etc. — see `backend/.env.example` and [`requirements-whisper-gpu.txt`](backend/requirements-whisper-gpu.txt). |
| **LiveKit**      | Backend: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `VOICE_API_BASE`, optional `VOICE_INTERNAL_SECRET`. Frontend: `NEXT_PUBLIC_LIVEKIT_URL`, `NEXT_PUBLIC_LIVEKIT_DEFAULT_ROOM`. |
| **MuseTalk**     | Dedicated `uvicorn app.musetalk.service_app:app --port 8001`; `MUSETALK_SERVICE_URL` on main API; `MUSETALK_*` / `MUSETALK_PYTHON`; `NEXT_PUBLIC_MUSETALK_ENABLED`. See **MuseTalk lip-sync**. |


Optional vendor paths under `.tools/` are documented in `backend/.env.example`.

## Tests

```bash
source backend/.venv/bin/activate
cd backend
pip install -r requirements-dev.txt   # if not already installed
python -m pytest tests/ -q
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
| `benchmark_musetalk.py`        | MuseTalk latency sweep (GPU)                                 |
| `simulate_lipsync_paths.py`    | Debug REST/WS/LiveKit lip-sync without the browser           |
| `e2e_process_edge_cases.py`    | Exercise `POST /process` variants against a running API        |
| `fix_musetalk_inference_image.py` | Patch upstream MuseTalk static-portrait bug               |
| `setup_musetalk_weights.sh`    | Idempotent HF / gdown weight fetch                           |
| `setup_ffmpeg_static.sh`       | Optional static FFmpeg for MuseTalk mux                      |
| `test_lipsync_8001.sh`         | Quick curl checks against MuseTalk on :8001                  |
| `qa_scenario_matrix.sh`        | Pytest subset + optional `RUN_HTTP=1`                        |


## API overview

Route handlers live under [`backend/app/routers/`](backend/app/routers/) and are mounted from [`backend/app/main.py`](backend/app/main.py); public paths are unchanged.

- **HTTP:** `POST /process`, `POST /conversation`, `POST /agent/summary`, `POST /tools/invoke`, `POST /stt`, `POST /tts`, `GET /livekit/token`, `GET /avatar/lipsync/status`, `POST /avatar/lipsync` (MuseTalk, optional), `POST /internal/voice/worker/transcript` (worker + `VOICE_INTERNAL_SECRET` only)
- **WebSocket:** `/ws/agent`, `/ws/conversation_audio`
- **Docs:** `GET /docs`

Default ports: API **8000**, Next **3000**, Ollama **11434**, LiveKit **7880**.

## Future improvements

- **WebSocket ASR:** keep finalize-then-transcribe for `/ws/conversation_audio`; optional client endpointing, interim UI channel, or server streaming decode without changing tool semantics.
- **LiveKit in production:** `wss://`, hardened keys, worker scaling.

