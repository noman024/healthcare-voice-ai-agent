import type { MutableRefObject } from "react";

import type { AgentPayload, CallSummaryPayload, TranscriptEntry } from "../callTypes";

export type CallTranscriptColumnProps = {
  transcript: TranscriptEntry[];
  liveCaption: string | null;
  transcriptEndRef: MutableRefObject<HTMLDivElement | null>;
  callSummary: CallSummaryPayload | null;
  summaryBusy: boolean;
  busy: boolean;
  roomReady: boolean;
  error: string | null;
  chatMode: "text" | "voice";
  setChatMode: (m: "text" | "voice") => void;
  text: string;
  setText: (t: string) => void;
  sendText: () => void;
  voiceBackend: "livekit" | "websocket";
  setVoiceBackend: (b: "livekit" | "websocket") => void;
  livekitPublicUrl: string;
  liveKitStatusOk: boolean | null;
  voiceSurfaceHint: string | null;
  setVoiceSurfaceHint: (hint: string | null) => void;
  userVoiceBackendPinnedRef: MutableRefObject<boolean>;
  syncSessionToPhone: boolean;
  setSyncSessionToPhone: (v: boolean) => void;
  returnSpeech: boolean;
  setReturnSpeech: (v: boolean) => void;
  useChunkedWsMic: boolean;
  setUseChunkedWsMic: (v: boolean) => void;
  conversationId: string;
  lkRoom: string;
  lkIdentity: string;
  sessionId: string;
  setSessionId: (s: string) => void;
  onAudioPick: (file: File | null) => void;
  result: AgentPayload | null;
  onFetchSummary: () => void;
  startMic: () => void;
  stopMicAndSend: () => void | Promise<void>;
  recording: boolean;
  voiceSessionOn: boolean;
};

export function CallTranscriptColumn({
  transcript,
  liveCaption,
  transcriptEndRef,
  callSummary,
  summaryBusy,
  busy,
  roomReady,
  error,
  chatMode,
  setChatMode,
  text,
  setText,
  sendText,
  voiceBackend,
  setVoiceBackend,
  livekitPublicUrl,
  liveKitStatusOk,
  voiceSurfaceHint,
  setVoiceSurfaceHint,
  userVoiceBackendPinnedRef,
  syncSessionToPhone,
  setSyncSessionToPhone,
  returnSpeech,
  setReturnSpeech,
  useChunkedWsMic,
  setUseChunkedWsMic,
  conversationId,
  lkRoom,
  lkIdentity,
  sessionId,
  setSessionId,
  onAudioPick,
  result,
  onFetchSummary,
  startMic,
  stopMicAndSend,
  recording,
  voiceSessionOn,
}: CallTranscriptColumnProps) {
  return (
    <section className="flex min-h-0 flex-1 flex-col bg-[#161616] lg:order-1">
      <div className="flex shrink-0 items-center justify-between border-b border-zinc-800 px-4 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Live transcript
        </h2>
        <button
          type="button"
          disabled={summaryBusy || busy || !roomReady}
          onClick={onFetchSummary}
          className="rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
        >
          {summaryBusy ? "…" : "Summary"}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {transcript.length === 0 && !liveCaption ? (
          <p className="text-center text-sm text-zinc-500">
            Your messages and the assistant will appear here with auto-scroll.
          </p>
        ) : null}
        <div className="mx-auto max-w-xl space-y-3">
          {transcript.map((line) => (
            <div
              key={line.id}
              className={`flex ${line.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[92%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                  line.role === "user"
                    ? "rounded-br-md bg-emerald-600/25 text-emerald-50"
                    : "rounded-bl-md border border-zinc-700/80 bg-zinc-800/80 text-zinc-100"
                }`}
              >
                <p className="mb-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
                  {line.role === "user" ? "You" : "Assistant"}
                </p>
                {line.text}
              </div>
            </div>
          ))}
          {liveCaption ? (
            <div className="flex justify-end opacity-80">
              <div className="max-w-[92%] rounded-2xl rounded-br-md border border-dashed border-zinc-600 bg-zinc-900/60 px-4 py-2 text-sm italic text-zinc-400">
                {liveCaption}
              </div>
            </div>
          ) : null}
          <div ref={transcriptEndRef} aria-hidden="true" className="h-1" />
        </div>
      </div>

      <div className="shrink-0 border-t border-zinc-800 bg-[#121212] p-3 md:p-4">
        {callSummary ? (
          <div className="mx-auto mb-3 max-w-2xl rounded-xl border border-zinc-800 bg-zinc-900/50 p-3 text-xs text-zinc-300">
            <p className="font-semibold text-zinc-200">Visit summary</p>
            <p className="mt-1 whitespace-pre-wrap text-zinc-400">{callSummary.summary}</p>
          </div>
        ) : null}

        {error ? (
          <p className="mx-auto mb-2 max-w-2xl rounded-lg border border-amber-900/50 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
            {error}
          </p>
        ) : null}

        <div className="mx-auto mb-4 flex max-w-2xl">
          <div className="inline-flex w-full rounded-xl border border-zinc-700 p-0.5 sm:w-auto">
            <button
              type="button"
              aria-pressed={chatMode === "text"}
              onClick={() => setChatMode("text")}
              disabled={busy}
              className={`flex-1 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors sm:flex-none sm:min-w-[7rem] ${
                chatMode === "text"
                  ? "bg-zinc-100 text-zinc-900"
                  : "text-zinc-400 hover:text-zinc-200"
              } disabled:opacity-40`}
            >
              Text chat
            </button>
            <button
              type="button"
              aria-pressed={chatMode === "voice"}
              onClick={() => setChatMode("voice")}
              disabled={busy}
              className={`flex-1 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors sm:flex-none sm:min-w-[7rem] ${
                chatMode === "voice"
                  ? "bg-teal-600 text-white"
                  : "text-zinc-400 hover:text-zinc-200"
              } disabled:opacity-40`}
            >
              Voice chat
            </button>
          </div>
        </div>

        {chatMode === "text" ? (
          <div className="mx-auto flex max-w-2xl flex-col gap-2 sm:flex-row sm:items-end">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={busy || !roomReady}
              rows={2}
              placeholder="Type a message…"
              className="min-h-[44px] flex-1 resize-none rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-2.5 text-sm text-white placeholder:text-zinc-500 focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600 disabled:opacity-50"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendText();
                }
              }}
            />
            <button
              type="button"
              disabled={busy || !roomReady}
              onClick={sendText}
              className="shrink-0 rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
            >
              Send
            </button>
          </div>
        ) : (
          <div className="mx-auto max-w-2xl space-y-3">
            <div>
              <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                Voice transport
              </p>
              <div className="inline-flex w-full rounded-xl border border-zinc-700 p-0.5 sm:w-auto">
                <button
                  type="button"
                  aria-pressed={voiceBackend === "websocket"}
                  onClick={() => {
                    userVoiceBackendPinnedRef.current = true;
                    setVoiceBackend("websocket");
                    setVoiceSurfaceHint("WebSocket voice — pause ~1s after speaking to send.");
                  }}
                  disabled={busy || !roomReady}
                  className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-colors sm:flex-none sm:min-w-[8rem] ${
                    voiceBackend === "websocket"
                      ? "bg-teal-600 text-white"
                      : "text-zinc-400 hover:text-zinc-200"
                  } disabled:opacity-40`}
                >
                  WebSocket
                </button>
                <button
                  type="button"
                  aria-pressed={voiceBackend === "livekit"}
                  onClick={() => {
                    userVoiceBackendPinnedRef.current = true;
                    setVoiceBackend("livekit");
                    setVoiceSurfaceHint("LiveKit selected — connecting…");
                  }}
                  disabled={busy || !roomReady || !livekitPublicUrl || liveKitStatusOk !== true}
                  title={
                    !livekitPublicUrl
                      ? "Set NEXT_PUBLIC_LIVEKIT_URL"
                      : liveKitStatusOk === null
                        ? "Checking LiveKit…"
                        : liveKitStatusOk === false
                          ? "LiveKit token service not available"
                          : "Room worker + RTC"
                  }
                  className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-colors sm:flex-none sm:min-w-[8rem] ${
                    voiceBackend === "livekit"
                      ? "bg-teal-600 text-white"
                      : "text-zinc-400 hover:text-zinc-200"
                  } disabled:opacity-40`}
                >
                  LiveKit
                </button>
              </div>
              {livekitPublicUrl && liveKitStatusOk === null ? (
                <p className="mt-1 text-[11px] text-zinc-500">Checking LiveKit token service…</p>
              ) : null}
            </div>
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/35 px-4 py-4 text-sm text-zinc-300">
              <p className="leading-relaxed">
                {voiceSurfaceHint ?? (!roomReady ? "Starting session…" : "Voice session starting…")}
              </p>
            </div>
          </div>
        )}

        <details className="mx-auto mt-3 max-w-2xl text-[11px] text-zinc-500">
          <summary className="cursor-pointer text-zinc-400">Advanced</summary>
          <div className="mt-2 space-y-2 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
            {chatMode === "text" ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy || recording || voiceSessionOn || !roomReady}
                  onClick={startMic}
                  className="rounded-lg border border-zinc-600 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40"
                  title="Push-to-talk — Text mode Advanced"
                >
                  Mic (push-to-talk)
                </button>
                <button
                  type="button"
                  disabled={busy || !recording}
                  onClick={() => void stopMicAndSend()}
                  className="rounded-lg bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-900 hover:bg-white disabled:opacity-40"
                >
                  Stop &amp; send
                </button>
              </div>
            ) : (
              <div className="space-y-1 text-zinc-500">
                <p>
                  Use <strong className="text-zinc-400">Text chat</strong> to type messages.
                  WebSocket voice auto-sends turns after a pause; you can talk over assistant TTS to
                  interrupt.
                </p>
                <p>
                  LiveKit path needs{" "}
                  <code className="font-mono text-zinc-400">run_voice_worker.py</code> and matching
                  env on API + worker.
                </p>
              </div>
            )}
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={syncSessionToPhone}
                onChange={(e) => setSyncSessionToPhone(e.target.checked)}
              />
              After identify, use phone as session ID (booking)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={returnSpeech}
                onChange={(e) => setReturnSpeech(e.target.checked)}
              />
              Spoken replies (TTS)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={useChunkedWsMic}
                onChange={(e) => setUseChunkedWsMic(e.target.checked)}
              />
              Text mode: send mic capture via WebSocket audio (recommended; avoid multi-part upload)
            </label>
            <p className="font-mono text-[10px] text-zinc-600">
              Room: {roomReady ? `${conversationId.slice(0, 8)}…` : "…"} · LiveKit room:{" "}
              <span className="break-all">{roomReady ? lkRoom : "…"}</span> · Identity:{" "}
              <span className="break-all">{lkIdentity}</span>
            </p>
            <label className="block">
              Session override
              <input
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 font-mono text-zinc-200"
              />
            </label>
            <label className="block">
              Upload audio
              <input
                type="file"
                accept="audio/*,.wav,.webm,.mp3,.ogg"
                disabled={busy || !roomReady}
                className="mt-1 block w-full text-zinc-400 file:mr-2 file:rounded file:border-0 file:bg-zinc-800 file:px-2 file:py-1"
                onChange={(e) => onAudioPick(e.target.files?.[0] ?? null)}
              />
            </label>
            {result ? (
              <details>
                <summary className="cursor-pointer text-zinc-400">Raw JSON</summary>
                <pre className="mt-2 max-h-40 overflow-auto rounded bg-black/40 p-2 text-[10px]">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </details>
            ) : null}
          </div>
        </details>
      </div>
    </section>
  );
}
