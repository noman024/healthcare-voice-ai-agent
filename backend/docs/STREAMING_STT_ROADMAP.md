# Streaming and chunked STT — incremental roadmap

This document describes how to evolve speech recognition toward **lower perceived latency** and **optional streaming transcripts** without breaking existing APIs. Implementation status: **design only**; the stack today uses **batch** `faster-whisper` transcription on finalized buffers (`transcribe_audio_bytes`).

## Contracts that must stay backward compatible

| Surface | Behavior to preserve |
|---------|---------------------|
| `POST /stt` | Multipart audio → JSON `{ text, language, warning }`. Clients relying on this shape must keep working when new features are disabled by default. |
| [`finalize_audio.iter_finalize_batch_turn_events`](app/conversation/finalize_audio.py) | Batch pipeline shared by **`/ws/conversation_audio`**, [`pipeline`](app/conversation/pipeline.py), and the LiveKit worker. Default path: **one STT pass** → agent events (`plan` / `tool` / `done`). |
| [`pipeline.iter_chunked_audio_turn_events`](app/conversation/pipeline.py) | Same event ordering semantics for chunked-WebSocket callers. |
| Agent runner | Consumes **one committed user text string per turn** (from STT). Partial hypotheses must not replace committed text until explicitly wired behind flags. |

## Current architecture (reference)

```text
binary audio buffer → bytes_stt.transcribe_audio_bytes → plain text → iter_turn_events → LLM/tools
```

[`bytes_stt`](app/audio/bytes_stt.py) wraps [`app.audio.stt`](app/audio/stt.py) (`faster-whisper`). There is **no** emission of interim tokens to the UI today.

## Incremental phases

### Phase A — Client-driven shorter finals (no backend schema change)

- Reduce silence tail or split recordings in the browser (`MediaRecorder` chunks, manual “stop speaking” UX).
- Each `finalize` still maps to one `transcribe()` call; **lower latency** comes from shorter audio duration, not streaming.

**Risk:** Low. No contract change.

### Phase B — Server-side segmentation (still batch STT per segment)

- Accumulate PCM/WebM server-side; apply **energy/VAD gate** (e.g. `webrtcvad`, Silero, or faster-whisper `vad_filter` over sliding windows).
- **`WHISPER_VAD_FILTER=1`** enables faster-whisper’s built-in **`vad_filter`** on every `transcribe()` (see [`app/audio/stt.py`](app/audio/stt.py)) — same REST/WS contracts; trims silence before decoding.
- Produce **multiple transcripts** per WebSocket session **or** concatenate segments before one agent turn.

**Compatibility:** Prefer emitting optional WebSocket events (`stt_segment`) only when `action=start` includes `"segment_mode": true`. Leave existing clients on single-transcript behavior.

### Phase C — Partial / streaming hypotheses (UX polish)

Options (pick one strategy per deployment):

1. **true streaming:** Use a stack that exposes partial results (different library or Whisper streaming wrappers). Route **committed** text into `iter_turn_events`; route partials only to UI (`type: stt_partial`).
2. **dual-model preview:** Tiny/fast model for **preview lines only**; `base`/`small` Whisper for **committed** transcript. Requires consistency checks and clear labeling in the UI.

**Compatibility:** Gate with env such as `STT_PARTIAL_EVENTS=0|1`. Agent input uses **committed** transcript only.

### Phase D — REST `/stt` extensions

If streaming ever exposes incremental JSON, use either:

- **New route** (`POST /stt/stream` WebSocket or SSE), or  
- Same route with query `?partial=1` returning newline-delimited JSON chunks,

while keeping default `POST /stt` identical.

## Implementation checklist (when coding)

1. Default-off flags so CI and [`scripts/e2e_integration_real.sh`](../scripts/e2e_integration_real.sh) unchanged.
2. Extend [`finalize_audio`](app/conversation/finalize_audio.py) in one place so LiveKit and WS stay aligned.
3. Add pytest for any new event types and regression tests for legacy single-`stt` flows ([`test_chunked_audio_pipeline_ws.py`](../tests/test_chunked_audio_pipeline_ws.py), [`test_finalize_audio.py`](../tests/test_finalize_audio.py)).

## Related latency work

Dual LLM planner/finalizer latency can be tuned independently via **`OLLAMA_PLANNER_MODEL`** / **`OLLAMA_FINALIZE_MODEL`** (see [`.env.example`](../.env.example)) and [`VALIDATION_REPORT.md`](../VALIDATION_REPORT.md).
