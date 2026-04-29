# Voice Healthcare Agent

- **Author:** MD Mutasim Billah Noman
- **Updated on:** 30 April 2026

Monorepo for a **web-based voice AI agent** for healthcare appointment booking: **FastAPI** backend and **Next.js** frontend.

- **Phase 1:** runnable skeleton and API health check from the UI.
- **Phase 2:** SQLite `appointments` table with `UNIQUE(date, time)`, indexed `phone`, and repository helpers (book / cancel / modify / list).
- **Phase 3:** Tool executor (`app/tools/`) wired to SQLite — all seven agent tools, validation, logging, and `POST /tools/invoke`.
- **Phase 4:** LLM agent (`app/llm/`, `app/agent/`): Ollama chat client, JSON planner output with retries, tool run + finalizer pass, rolling session memory (10 turns), `POST /agent/turn`.
- **Phase 5:** STT/TTS — [`faster-whisper`](backend/app/audio/stt.py) for `POST /stt`, [Piper](https://github.com/rhasspy/piper) CLI for `POST /tts`, plus `GET /health/llm` to probe Ollama.
- **Phase 6:** Conversation pipeline — `POST /process` (JSON) and `POST /conversation` (multipart: text or audio → agent, optional Piper WAV).
- **Phase 7:** Frontend [call lab](frontend/app/call/page.tsx) — text and audio file to the agent (`/process`, `/conversation`).
- **Phase 8:** **LiveKit (optional)** — `GET /livekit/token` + Python [`livekit_agent_worker.py`](backend/scripts/livekit_agent_worker.py) (browser mic to the same finalize-batch pipeline as **`/ws/conversation_audio`**); shared layer [`finalize_audio.py`](backend/app/conversation/finalize_audio.py). REST/WebSocket stay the primary fallback.


## Prerequisites

- **Python** 3.11+ (3.12 recommended)
- **Node.js** **18.17+** or **20+** and npm (frontend uses **Next.js 14** so **Node 18** works; **Next.js 16** is not used because it requires Node **≥ 20.9**)
- **Docker** + **Docker Compose** v2 (`docker compose`), optional — only needed to run **[LiveKit via Docker](#docker-livekit-server-webrtc-signaling)**; the FastAPI backend and Ollama can run entirely without containers.

## Repository layout

| Path | Role |
|------|------|
| `backend/` | FastAPI app, SQLite appointments DB (`app/db/`), [`app/conversation/finalize_audio.py`](backend/app/conversation/finalize_audio.py), optional [`app/livekit/`](backend/app/livekit/) (protocol + worker helpers) |
| `frontend/` | Next.js (App Router) UI |
| `scripts/run_with_tools.sh` | Prepend `.tools/ollama` to `PATH` / `LD_LIBRARY_PATH`, then run a command |

### Vendor installs (`.tools/`)

Put **Ollama** and **Piper** under **`/.tools/`** at the repo root (not committed — see `.gitignore`):

- **Ollama:** `.tools/ollama/bin/ollama` (libraries under `.tools/ollama/lib/ollama/`). Run e.g. `./scripts/run_with_tools.sh ollama serve` so the binary resolves shared libs without a system install.
- **Piper:** `.tools/piper/piper/piper` and voices such as `.tools/piper/voices/en_US-lessac-low.onnx`. Set `PIPER_BINARY` and `PIPER_VOICE` in `backend/.env` to those paths (see `.env.example`).

## Managing runtime components

Everything below assumes the **repo root** as the working directory unless noted. Use **one** FastAPI process on **:8000** (if you restart the API after code or `.env` changes, stop the old `uvicorn` first to avoid *address already in use*).

### Port map

| Port | Service |
|------|---------|
| **3000** | Next.js dev server (`npm run dev` in `frontend/`) |
| **8000** | FastAPI (`uvicorn` from `backend/`) |
| **7880** | LiveKit HTTP / WebSocket signal (Docker compose) |
| **7881** | LiveKit TCP (RTC helper) |
| **7882/udp** | LiveKit WebRTC media (Docker compose) |
| **11434** | Ollama HTTP API (default) |

### Python dependencies (backend)

| Install | Command | When |
|---------|---------|------|
| Core API, agent, STT, tools | `cd backend && pip install -r requirements.txt` | Always |
| LiveKit (JWT + RTC worker: `GET /livekit/token`, [`livekit_agent_worker.py`](backend/scripts/livekit_agent_worker.py)) | `pip install -r requirements-livekit.txt` | Optional; installs **`livekit`** + **`livekit-api`**. Without it, token route / worker are unavailable (see table below). |

### Ollama (LLM)

- **Install:** Prefer the repo-vendored binary under `.tools/ollama/` (not on your global `PATH` unless you install Ollama system-wide). Use the wrapper so shared libraries resolve:

  ```bash
  ./scripts/run_with_tools.sh ollama serve
  ```

- **Model:** Pull the tag configured in `OLLAMA_MODEL`:

  ```bash
  ./scripts/run_with_tools.sh ollama pull qwen2.5:7b-instruct
  ```

- **Check:** With the API running, `GET /health/llm` should return **200** and `{"ollama":"ok",...}`. `POST /agent/turn` and `POST /process` need Ollama up and the model pulled.

### Docker: LiveKit server (WebRTC signaling)

The repo ships **`docker-compose.livekit.yml`** at the **repository root** (not under `backend/`). It runs `livekit/livekit-server` in **`--dev`** mode for local WebRTC experiments.

1. **Start** (foreground or daemon):

   ```bash
   docker compose -f docker-compose.livekit.yml up -d
   ```

   Use `docker compose ... logs -f livekit` once to read startup lines. In `--dev` mode the server often logs placeholder API credentials (commonly **`devkey`** / **`secret`**); treat these as **local-only**, not production secrets.

2. **Stop / remove container:**

   ```bash
   docker compose -f docker-compose.livekit.yml down
   ```

3. **Backend env:** Set in `backend/.env` (keys must match the LiveKit server):

   | Variable | Purpose |
   |----------|---------|
   | `LIVEKIT_API_KEY` | API key accepted by the LiveKit server |
   | `LIVEKIT_API_SECRET` | API secret used to sign participant JWTs |

4. **Python:** `pip install -r requirements-livekit.txt` so `GET /livekit/token` can mint tokens.

5. **Verify token API** (API on :8000, keys set, `livekit-api` installed):

   ```bash
   curl -sS "http://127.0.0.1:8000/livekit/token?room=demo&identity=tester"
   ```

   Expect **200** and a JSON body containing a JWT string. **503** usually means missing keys, missing `livekit-api`, or a signing error.

6. **Frontend:** In `frontend/.env.local`, set **`NEXT_PUBLIC_LIVEKIT_URL=ws://127.0.0.1:7880`** (or your host) so the browser client matches the Docker-published port. Keep **`NEXT_PUBLIC_API_URL`** pointed at the FastAPI origin (e.g. `http://localhost:8000`). Use a **fixed room name** on `/call` (default **`healthcare-demo`**) — it MUST match **`LIVEKIT_ROOM`** for the Python agent worker below. Optional **`NEXT_PUBLIC_LIVEKIT_AGENT_TOPIC`** (default **`lk-agent-v1`**) matches the worker’s reliable data-channel topic.

7. **Optional — Python agent worker (mic → STT → LLM/tools):**

   Requires **`pip install -r requirements-livekit.txt`** (installs **`livekit`** + **`livekit-api`**).

   Terminal (from `backend/`, `.env` loading `DATABASE_PATH`, `OLLAMA_*`, `LIVEKIT_*`):

   ```bash
   export LIVEKIT_ROOM=healthcare-demo LIVEKIT_AGENT_IDENTITY=agent-worker LIVEKIT_URL=ws://127.0.0.1:7880
   PYTHONPATH=. python scripts/livekit_agent_worker.py
   ```

   Then open **[http://localhost:3000/call](http://localhost:3000/call)** → LiveKit panel → enter the **same room name** → Connect → Publish mic → **Send start** (buffer) → speak briefly → **Send finalize** → JSON events mirror `/ws/conversation_audio` over the **`lk-agent-v1`** data topic (`audio_wav_base64` stripped for size). Minimal connectivity without the pipeline stays available via **`scripts/livekit_worker_stub.py`**.


### Recommended local startup order

1. **Ollama** — `./scripts/run_with_tools.sh ollama serve` (separate terminal).
2. **LiveKit** (optional) — `docker compose -f docker-compose.livekit.yml up -d`, then sync **`LIVEKIT_*`** in `backend/.env` so **`LIVEKIT_ROOM`** matches the **`/call`** room name (**`healthcare-demo`** by default).
3. **Backend** — `cd backend && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000`.
4. **LiveKit agent worker** (optional WebRTC ↔ pipeline): `cd backend`, same venv, `pip install -r requirements-livekit.txt`, then `PYTHONPATH=. python scripts/livekit_agent_worker.py` (loads `backend/.env` including **`LIVEKIT_URL`** / **`LIVEKIT_ROOM`**). Run **before or after** the browser connects to the room.
5. **Frontend** — `cd frontend && npm run dev`.

### Operational scripts

| Script | Purpose |
|--------|---------|
| `backend/scripts/e2e_integration_real.sh` | Strict smoke: **`/health`**, **`/health/llm`**, **`/tools/invoke`**, **`/stt`**, **`/tts`**, **`/agent/turn`**, multipart **`/conversation`** — requires Ollama + Piper (+ Whisper weights) configured; exits non-zero on failure |
| `backend/scripts/benchmark_api_performance.py` | Timings for production routes; **`--fail-if-any-route-mean-ms-above N`** exits **1** if any sequential route mean exceeds **N ms** (optional CI / warm-stack smoke SLA; default **0** = disabled) |
| `backend/scripts/e2e_process_edge_cases.py` | Agent + SQLite regression (mix of **`POST /process`** and **`POST /tools/invoke`**); requires API + model; see docstring |
| `backend/scripts/e2e_real_smoke.sh` | Lenient smoke (missing Ollama/TTS may still exit 0) |
| `backend/scripts/livekit_agent_worker.py` | WebRTC **agent** bridge: subscribes to browser mic, runs same pipeline as **`/ws/conversation_audio`**; needs **`livekit`** pip + **`LIVEKIT_ROOM`** matching the UI (see [Docker: LiveKit server](#docker-livekit-server-webrtc-signaling)) |
| `backend/scripts/livekit_worker_stub.py` | Minimal LiveKit room join (connectivity only; no STT). Use **`livekit_agent_worker.py`** for the full pipeline |
| `backend/scripts/verify_ollama_agent.sh` | Quick **`/agent/turn`** sanity check |

## Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Optional — LiveKit (JWT + agent worker): pip install -r requirements-livekit.txt
cp .env.example .env
# Edit `.env`: replace `<REPO_ROOT>` in PIPER_* with your monorepo absolute path (or paste paths from `.tools/`).
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **Health:** [http://localhost:8000/health](http://localhost:8000/health)
- **OpenAPI:** [http://localhost:8000/docs](http://localhost:8000/docs)

### Environment (`backend/.env`)

| Variable | Description |
|----------|-------------|
| `APP_ENV` | `development` (placeholder) |
| `WARMUP_MODELS` | `1` (default): on API startup, load **Whisper**, one short **Ollama** completion, and one **Piper** utterance so first user traffic is faster. Set `0` for instant startup (pytest sets `0`). |
| `CORS_ORIGINS` | Comma-separated origins; default allows `http://localhost:3000` |
| `LOG_ENABLED` | `1` (default): write rotating **`app.*`** logs to **`<repo>/logs/voice-agent-api.log`**; `0` disables (pytest disables by default via `tests/conftest.py`). Restart **uvicorn** after changing logging env vars. |
| `LOG_LEVEL` | `INFO` (default), `DEBUG`, or `WARNING` — minimum level emitted to that file handler for **`app`** and descendant loggers. |
| `LOG_UVICORN` | `0` (default): only application (`app`) logs land in **`voice-agent-api.log`**. Set `1` to also append **access/error** from uvicorn onto the **same** rotating handler. |
| `OLLAMA_BASE_URL` | Ollama **origin** only (default `http://localhost:11434`). Do **not** add `/api` or `/v1` — the backend builds paths like `/api/chat` itself. |
| `OLLAMA_MODEL` | Ollama chat tag (`qwen2.5:7b-instruct` recommended default. Run **`ollama pull <tag>`** before relying on planner output. |

#### LLM model choice {#llm-model-choice}

The planner emits **exact JSON** (via `response_format` with Ollama) and retries on parse failures. For **local** workloads, benchmarks and practitioner notes generally rank **`qwen2.5:7b-instruct`** strongest on **VRAM vs structured-output fidelity** (~4–5 GiB quantized on typical rigs). Larger **`qwen2.5:14b-instruct`** can improve refusal and edge naming; **`qwen2.5:3b-instruct`** minimizes latency/memory but may need more retries on harder turns. Llama **`llama3.1:8b-instruct`** is a credible alternative where JSON rigidity dominates (validate on your hardware).

Rough selection:

| Scenario | Typical tag |
|----------|-------------|
| **Default balance** — production-style voice assistant | **`qwen2.5:7b-instruct`** |
| Tight VRAM / low latency experiments | **`qwen2.5:3b-instruct`** |
| Highest local quality on ample VRAM | **`qwen2.5:14b-instruct`** or **`llama3.1:8b-instruct`** |

| Variable | Description |
|----------|-------------|
| `OLLAMA_REQUEST_TIMEOUT_S` | Per-request HTTP timeout to Ollama (default **300** seconds; planner + finalizer each need a completion) |
| `WHISPER_MODEL` | Whisper size: `tiny`, `base`, … (default `base`) |
| `WHISPER_DEVICE` | `auto` (default): use all visible CUDA GPUs via `device_index=[0,1,…]` when available, else CPU. Set `cpu` or `cuda` to force. |
| `WHISPER_DEVICE_INDICES` | Optional comma list (e.g. `0,1,3`). When unset, `auto`/`cuda` uses every GPU CTranslate2 sees. |
| `WHISPER_COMPUTE_TYPE` | Optional; default `float16` on GPU, `int8` on CPU. If CUDA load **or** inference fails (e.g. missing `libcublas.so.12` in `LD_LIBRARY_PATH`), STT reloads on **CPU** automatically and continues. |
| `WHISPER_NUM_WORKERS` | Optional CTranslate2 worker threads (default: **one per visible GPU** when on CUDA, capped by `WHISPER_MAX_WORKERS`, for concurrent `transcribe()`; override to tune throughput). |
| `WHISPER_MAX_WORKERS` | Cap for auto `WHISPER_NUM_WORKERS` on GPU (default `8`). |
| `WHISPER_CPU_NUM_WORKERS` | CPU / fallback worker count (default `1`). |
| `WHISPER_VAD_FILTER` | Set `1` / `true` / `yes` / `on` to enable faster-whisper **`vad_filter`** on transcribe (trims silence; can change timings vs default off). |
| `CUDA_HOME` / `CUDA_LIBRARY_PATH` | Prepends all common **`lib64`** / **`targets/x86_64-linux/lib`** paths (CUDA 12 and 13 layouts) to **`LD_LIBRARY_PATH`**. CTranslate2 wheels often require **`libcublas.so.12`**; if only CUDA 13 (**.so.13**) is installed, either add a **CUDA 12 compatibility** runtime or use **`WHISPER_DEVICE=cpu`**. |
| `OLLAMA_INFER_DEVICE` | `auto` (default): request max GPU layers from Ollama (`num_gpu=-1`). `cpu` sends `num_gpu=0`. |
| `OLLAMA_NUM_GPU_LAYERS` | Layer offload count for Ollama (default **-1** = as many as the server will place on GPU). |
| `OLLAMA_OPTIONS_JSON` | JSON object merged into Ollama `/api/chat` `options` (overrides `num_gpu` if set). |
| `PIPER_CUDA` | `auto` (default): allow GPU Piper when CTranslate2 sees CUDA. `off` avoids GPU hints. |
| `PIPER_CUDA_STRATEGY` | **`all`** (default): do **not** set `CUDA_VISIBLE_DEVICES` so the Piper subprocess sees **every** GPU. `round_robin`: pin one GPU per request for load spreading. |
| `PIPER_CUDA_CPU_FALLBACK` | `1` (default): if Piper fails, retry with CPU-friendly env. |
| `PIPER_BINARY` | Piper executable (default: `piper` on `PATH`) |
| `PIPER_VOICE` | Absolute path to Piper `.onnx` voice file |
| `DATABASE_PATH` | SQLite file path (default `data/appointments.db`, relative to **current working directory** — run uvicorn from `backend/`) |
| `CONVERSATION_PERSIST` | `0` (default): session memory is **in-process only**. `1`: append each finalized turn pair to **`conversation_messages`** in the same SQLite file and hydrate empty in-memory sessions on next request (restart-safe rolling transcript for **`session_id`**) |
| `CONVERSATION_PERSIST_MAX_MESSAGES` | Optional max **chat messages** persisted per session (default **20**, same ceiling as rolling memory). Rows beyond this are pruned after each insert |
| `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` | Optional rotation limits (defaults 10 MiB, 4 backups). |
| `SLOT_OPEN_HOUR`, `SLOT_CLOSE_HOUR`, `SLOT_STEP_MINUTES` | Optional — template grid for `fetch_slots` (defaults `9`, `17`, `30`) |
| `LIVEKIT_API_KEY` | Optional — LiveKit HTTP API **key**; must match `docker-compose.livekit.yml` (or cloud) server. Needed for **`GET /livekit/token`** with **`pip install -r requirements-livekit.txt`**. |
| `LIVEKIT_API_SECRET` | Optional — signing secret paired with **`LIVEKIT_API_KEY`** for participant JWTs. |
| `LIVEKIT_URL` | Signaling WebSocket URL for the agent worker (local default **`ws://127.0.0.1:7880`**). |
| `LIVEKIT_ROOM` | Room for **`livekit_agent_worker.py`** — **must match** the **Room name** on **`/call`** (UI default **`healthcare-demo`**). |
| `LIVEKIT_AGENT_IDENTITY` | JWT `identity` for the worker (default **`agent-worker`**); must differ from browser participants. |
| `LIVEKIT_AGENT_DATA_TOPIC` | Reliable data topic for agent control + events (default **`lk-agent-v1`**); match **`NEXT_PUBLIC_LIVEKIT_AGENT_TOPIC`** in the frontend. |

#### Phone numbers (tools)

`identify_user`, booking, and lookup tools accept **international-format** numbers: after stripping spaces and punctuation, **8–15 subscriber digits** (ITU/E.164 envelope). Include a **country calling code** (e.g. `+44 …`, `+91 …`, `+1 …`) when speaking with the model or use a full national digit string that still fits that range. Storage keeps a leading `+` when the user (or model) included it.

#### CUDA 12 libraries for GPU Whisper (`libcublas.so.12`)

[faster-whisper](backend/app/audio/stt.py) (CTranslate2) on Linux often loads **`libcublas.so.12`**. If the machine only has **`libcublas.so.13`** (or another major), either:

- Install a **CUDA 12** runtime or toolkit so **`libcublas.so.12`** exists, and ensure its directory is visible via **`CUDA_HOME` / `CUDA_LIBRARY_PATH`** (the backend also prepends common layout paths), **or**
- Set **`WHISPER_DEVICE=cpu`** to use CPU STT without that library (see log line `stt_cuda_runtime_failed_reloading_cpu` if GPU load failed).

For a full CUDA 12 toolkit layout, use [NVIDIA CUDA downloads](https://developer.nvidia.com/cuda-downloads) (Linux `.deb` / `.run`) and point **`CUDA_LIBRARY_PATH`** at the toolkit’s `targets/x86_64-linux/lib/` (or equivalent) if it is not under `/usr/local/cuda`.

### Tools (`POST /tools/invoke`)

JSON body: `{ "tool": "<name>", "arguments": { ... } }`.

Supported tools: `identify_user`, `fetch_slots`, `book_appointment`, `retrieve_appointments`, `cancel_appointment`, `modify_appointment`, `end_conversation`.

Example:

```bash
curl -sS -X POST http://localhost:8000/tools/invoke \
  -H 'Content-Type: application/json' \
  -d '{"tool":"fetch_slots","arguments":{"date":"2026-06-01"}}'
```

### Agent (`POST /agent/turn`)

Body: `{ "message": "user text", "session_id": "optional-id" }`.

Requires **Ollama** running with the model in `OLLAMA_MODEL`. Use a **Qwen instruct** variant (e.g. `ollama pull qwen2.5:7b-instruct`) so the planner returns valid JSON and fewer bad tool calls. Returns `final_response`, structured `plan`, and optional `tool_execution`.

**Multi-GPU (Ollama):** leave `CUDA_VISIBLE_DEVICES` unset so the **Ollama server** sees every GPU. The API sends `options.num_gpu` from `OLLAMA_NUM_GPU_LAYERS` (default **-1**) so Ollama can offload as many layers as it chooses; set `OLLAMA_INFER_DEVICE=cpu` to force CPU. For concurrent generations, set the server env **`OLLAMA_NUM_PARALLEL`** (and related Ollama server tuning) on the `ollama serve` process — see [Ollama docs](https://github.com/ollama/ollama).

**STT (Whisper):** with `WHISPER_DEVICE=auto`, CTranslate2 uses **all** visible GPUs (`device_index=[0,…]`) and sets `num_workers` to match for concurrent decode when many requests hit `/stt`.

**TTS (Piper):** with **`PIPER_CUDA_STRATEGY=all`** (default), Piper subprocesses see **all** GPUs (no `CUDA_VISIBLE_DEVICES` pin). Use **`round_robin`** only if you want to spread many concurrent `/tts` calls across cards. CPU-only Piper builds ignore GPU.

**Manual Ollama check** (with `uvicorn` on port 8000):

```bash
curl -sS http://127.0.0.1:8000/health/llm
# Expect: {"ollama":"ok", ...} when ollama serve is running

./scripts/verify_ollama_agent.sh
```

### Speech (`POST /stt`, `POST /tts`)

- **`/stt`** — `multipart/form-data`: field `audio` (WAV/WebM/MP3/…); optional field `language` (e.g. `en`). Uses **faster-whisper** (`WHISPER_MODEL` defaults to `base`; use `tiny` for faster CPU tests).
- **`/tts`** — JSON `{"text":"..."}` returns `audio/wav` when **Piper** is installed and `PIPER_VOICE` points to a `.onnx` model file. If unset, returns **503** with setup instructions.

### Conversation (`POST /process`, `POST /conversation`)

- **`/process`** — JSON: `{ "message", "session_id"?, "return_speech"? }`. Text-only agent turn; set `return_speech: true` to include base64 WAV when Piper is configured.
- **`/conversation`** — `multipart/form-data`: either field **`audio`** (file: STT → agent) or **`message`** (string). Optional `language`, `return_speech` (default `true`). Same response shape as the pipeline (agent output + optional `audio_wav_base64`).

## Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

If `next dev` fails with **Cannot find module `./NNN.js`**, the `.next` cache is out of sync — run `npm run clean` (or `rm -rf .next`) and start again.

Open [http://localhost:3000](http://localhost:3000). The home page calls `${NEXT_PUBLIC_API_URL}/health`; ensure the backend is running first for a green status. **[http://localhost:3000/call](http://localhost:3000/call)** exercises `/process` and `/conversation` from the browser.

### Environment (`frontend/.env.local`)

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Backend base URL (default in example: `http://localhost:8000`) |
| `NEXT_PUBLIC_LIVEKIT_URL` | WebSocket URL for the LiveKit signal channel (Docker dev default: **`ws://127.0.0.1:7880`**). Omit or empty if not using LiveKit UI. |
| `NEXT_PUBLIC_LIVEKIT_AGENT_TOPIC` | Optional — must match **`LIVEKIT_AGENT_DATA_TOPIC`** on **`livekit_agent_worker.py`** (default **`lk-agent-v1`**) for JSON control + events. |

### Updating dependencies (Node / Python)

- **Frontend:** From `frontend/`: `npm install` after cloning or pulling lockfile changes. Use `npm run clean` (or `rm -rf .next`) if dev server errors on stale artifacts.
- **Backend:** From `backend/` with the venv active: `pip install -r requirements.txt` (+ `pip install -r requirements-livekit.txt` when enabling LiveKit tokens). Upgrade OS-level **CUDA** toolkits independently if GPU STT/Ollama need newer drivers/libs (see Whisper/CUDA sections above).

## Full stack smoke (real HTTP, DB, Whisper)

Complete stack setup follows **[Managing runtime components](#managing-runtime-components)** (Docker LiveKit, Ollama wrapper, backend/frontend installs). Then:

1. **Backend** (from `backend/`): `source .venv/bin/activate && pip install -r requirements.txt && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
2. **Ollama** (for `/agent/turn` and **200** on `/health/llm`): e.g. `./scripts/run_with_tools.sh ollama serve`, then `./scripts/run_with_tools.sh ollama pull <tag>` where `<tag>` is `OLLAMA_MODEL` in `backend/.env`.
3. **Piper** (for `/tts` and spoken pipeline responses): point `PIPER_BINARY` / `PIPER_VOICE` at `.tools/piper` paths (see `.env.example`).
4. **LiveKit (optional)** — for **`GET /livekit/token`** and WebRTC UI: `docker compose -f docker-compose.livekit.yml up -d`, set **`LIVEKIT_*`** + `pip install -r requirements-livekit.txt`, **`NEXT_PUBLIC_LIVEKIT_URL`** in the frontend — see **[Docker: LiveKit server](#docker-livekit-server-webrtc-signaling)**.
5. With the API on port **8000**, run the **lenient** smoke (allows missing Ollama/TTS):

```bash
cd backend && ./scripts/e2e_real_smoke.sh
```

**Typical results without optional services:** `/health`, `/tools/invoke`, `/stt` → **200**; `/health/llm` and `/tts` may return **503**; `/agent/turn` → **502** until Ollama is running (script still exits **0**).

6. **Strict live E2E** (no mocks — needs Ollama + Piper + model weights; exits non-zero if anything returns non-200):

```bash
cd backend && ./scripts/e2e_integration_real.sh
```

7. **Agent + DB edge regression** (`/process`, `/tools/invoke`; requires API + downloaded `OLLAMA_MODEL`):

```bash
cd backend && source .venv/bin/activate && API_BASE=http://127.0.0.1:8000 python scripts/e2e_process_edge_cases.py
```

8. **Performance benchmark** (all real HTTP calls; reports mean/min/max ms per route + optional `/health` throughput):

```bash
cd backend && source .venv/bin/activate
python scripts/benchmark_api_performance.py --rounds 5 --concurrent 16
```

9. **Frontend:** `cd frontend && npm install && npm run build` (or `npm run dev`).

## Verify

1. Start **backend** on port **8000**.
2. Start **frontend** on port **3000**.
3. Visit the frontend: you should see **API healthy** and `{ "status": "ok" }`.

## Backend tests

From `backend/` with the virtualenv active:

```bash
python -m pytest tests/ -v
```

These tests cover Phases **2–8**: DB, tools, LLM parser, mocked agent turns, audio API mocks, conversation WebSocket chunks, shared **finalize** pipeline (`finalize_audio`), LiveKit token route + data-channel protocol, and related helpers. `pytest` forces `WHISPER_DEVICE=cpu` via `conftest.py` so suites stay fast. Full stack validation without mocks uses **`./scripts/e2e_integration_real.sh`** above.

## Next phases

- **Incremental / streaming ASR** over WebSocket or WebRTC media (current path: **finalize-then-batch** STT + agent, including **[`livekit_agent_worker.py`](backend/scripts/livekit_agent_worker.py)**).
- **Prod LiveKit** — non-dev keys, **`wss://`**, monitoring, horizontal scaling of agent workers.
- **Summaries & analytics** — richer post-call summaries, optional export, deployment hardening beyond local smoke scripts.
