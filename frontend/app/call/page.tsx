"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import AvatarVoice from "./AvatarVoice";
import LiveKitPanel from "./LiveKitPanel";

const defaultApiUrl = "http://localhost:8000";

type FeedTone = "neutral" | "ok" | "warn";

type FeedItem = { id: string; label: string; tone: FeedTone };

type AgentPayload = Record<string, unknown>;

function httpToWsBase(httpUrl: string): string {
  return httpUrl.trim().replace(/\/$/, "").replace(/^http/, "ws");
}

function summarizeTool(tool: unknown): string {
  const t = typeof tool === "string" ? tool : "";
  const labels: Record<string, string> = {
    none: "Thinking…",
    identify_user: "Identifying user…",
    fetch_slots: "Fetching available slots…",
    book_appointment: "Booking appointment…",
    retrieve_appointments: "Loading your appointments…",
    cancel_appointment: "Cancelling appointment…",
    modify_appointment: "Updating appointment…",
    end_conversation: "Ending conversation…",
  };
  return labels[t] ?? (t ? `Running ${t}…` : "Updating…");
}

function transcriptAndReply(
  result: AgentPayload | null,
): { user: string | null; assistant: string | null } | null {
  if (!result || typeof result !== "object") return null;
  const rawUser = typeof result.transcript === "string" ? result.transcript.trim() : "";
  const rawAsst = typeof result.final_response === "string" ? result.final_response.trim() : "";
  const user = rawUser.length > 0 ? rawUser : null;
  const assistant = rawAsst.length > 0 ? rawAsst : null;
  if (!user && !assistant) return null;
  return { user, assistant };
}

function buildActivityFeed(result: AgentPayload): FeedItem[] {
  const out: FeedItem[] = [];
  const planRaw = result.plan;
  const plan =
    planRaw && typeof planRaw === "object" ? (planRaw as Record<string, unknown>) : null;

  const intent =
    typeof plan?.intent === "string" ? plan.intent : typeof result.intent === "string" ? result.intent : "";
  if (intent) {
    out.push({ id: "intent", label: `Intent: ${intent}`, tone: "neutral" });
  }

  const tool = typeof plan?.tool === "string" ? plan.tool : "";
  if (tool && tool !== "none") {
    out.push({ id: "tool-plan", label: summarizeTool(tool), tone: "neutral" });
  }

  const teRaw = result.tool_execution;
  if (teRaw && typeof teRaw === "object") {
    const te = teRaw as Record<string, unknown>;
    const ok = te.success === true;
    const errMsg =
      typeof (te.error as Record<string, unknown> | undefined)?.message === "string"
        ? String((te.error as { message?: string }).message)
        : "Action failed.";
    const toolTag = typeof te.tool === "string" ? te.tool : tool;
    if (toolTag) {
      out.push({
        id: "tool-done",
        label: ok ? `${toolTag} succeeded ✓` : `${toolTag}: ${errMsg}`,
        tone: ok ? "ok" : "warn",
      });
    }
  }

  const warn = typeof result.warning === "string" ? result.warning : "";
  if (warn) {
    out.push({ id: "warn", label: warn, tone: "warn" });
  }

  return out;
}

async function playWavBase64(base64: string): Promise<void> {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: "audio/wav" });
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  try {
    await audio.play();
    await new Promise<void>((resolve) => {
      audio.onended = () => resolve();
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

export default function CallPage() {
  const baseUrl = (process.env.NEXT_PUBLIC_API_URL ?? defaultApiUrl).replace(/\/$/, "");
  const wsBase = httpToWsBase(baseUrl);

  const [sessionId, setSessionId] = useState("web-call-ui");
  const [syncSessionToPhone, setSyncSessionToPhone] = useState(true);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [streamSteps, setStreamSteps] = useState(false);
  const [useChunkedWsMic, setUseChunkedWsMic] = useState(false);
  const [returnSpeech, setReturnSpeech] = useState(true);

  const [micVizStream, setMicVizStream] = useState<MediaStream | null>(null);

  const [result, setResult] = useState<AgentPayload | null>(null);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [summaryText, setSummaryText] = useState<string | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [wsLog, setWsLog] = useState<string>("");

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [recording, setRecording] = useState(false);

  useEffect(() => {
    const feedItems = result ? buildActivityFeed(result) : [];
    setFeed(feedItems);
  }, [result]);

  /** When ``identify_user`` succeeds, server returns ``session_identity.suggested_session_id`` (normalized phone). */
  useEffect(() => {
    if (!result || !syncSessionToPhone) return;
    const si = result["session_identity"] as { suggested_session_id?: string } | undefined;
    const next = typeof si?.suggested_session_id === "string" ? si.suggested_session_id.trim() : "";
    if (next && next !== sessionId.trim()) setSessionId(next);
  }, [result, syncSessionToPhone, sessionId]);

  const attachAudio = useCallback(
    async (data: AgentPayload) => {
      const b64 = typeof data.audio_wav_base64 === "string" ? data.audio_wav_base64 : null;
      const ok = typeof data.tts_configured === "boolean" ? data.tts_configured : true;
      if (!returnSpeech || !b64 || !ok) return;
      try {
        setSpeaking(true);
        await playWavBase64(b64);
      } catch {
        /* autoplay blocked or decode error — ignore */
      } finally {
        setSpeaking(false);
      }
    },
    [returnSpeech],
  );

  const sendTextRest = useCallback(async () => {
    const msg = text.trim();
    const sid = sessionId.trim() || "default";
    if (!msg) return;
    setError(null);
    setBusy(true);
    setResult(null);
    setSummaryText(null);
    try {
      const res = await fetch(`${baseUrl}/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          session_id: sid,
          return_speech: returnSpeech,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as AgentPayload;
      if (!res.ok) {
        setError(`HTTP ${res.status}: ${JSON.stringify(data)}`);
        return;
      }
      setResult(data);
      await attachAudio(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }, [attachAudio, baseUrl, returnSpeech, sessionId, text]);

  const sendTextViaWebSocket = useCallback(async () => {
    const msg = text.trim();
    const sid = sessionId.trim() || "default";
    if (!msg) return;
    setError(null);
    setBusy(true);
    setResult(null);
    setSummaryText(null);
    setWsLog("");

    return new Promise<void>((resolve) => {
      let feedAcc: FeedItem[] = [];

      try {
        const ws = new WebSocket(`${wsBase}/ws/agent`);
        ws.onerror = () => {
          setError("WebSocket error (is the API reachable?)");
          setBusy(false);
          resolve();
        };
        ws.onopen = () => {
          ws.send(JSON.stringify({ action: "turn", message: msg, session_id: sid }));
          setWsLog("sent turn");
        };
        ws.onmessage = (ev: MessageEvent) => {
          let payload: AgentPayload;
          try {
            payload = JSON.parse(String(ev.data)) as AgentPayload;
          } catch {
            return;
          }
          const t = typeof payload.type === "string" ? payload.type : "";
          if (t === "plan" && payload.plan && typeof payload.plan === "object") {
            feedAcc = buildActivityFeed({
              intent: (payload.plan as { intent?: string }).intent,
              plan: payload.plan as Record<string, unknown>,
              tool_execution: null,
            }).filter((x) => x.id !== "tool-done");
            setFeed(feedAcc);
          }
          if (t === "tool" && typeof payload.tool_execution === "object") {
            feedAcc = [...feedAcc];
            feedAcc.push(...buildActivityFeed({ plan: {}, tool_execution: payload.tool_execution }));
            setFeed(feedAcc);
          }
          if (t === "done") {
            ws.close();
            setResult(payload as AgentPayload);
            setBusy(false);
            void attachAudio(payload as AgentPayload);
            resolve();
          }
          if (t === "error") {
            const m = typeof payload.message === "string" ? payload.message : "WebSocket error";
            setError(m);
            ws.close();
            setBusy(false);
            resolve();
          }
        };
        ws.onclose = () => {};
      } catch (e) {
        setError(e instanceof Error ? e.message : "WebSocket failed");
        setBusy(false);
        resolve();
      }
    });
  }, [attachAudio, wsBase, sessionId, text]);

  const sendText = useCallback(async () => {
    if (streamSteps) await sendTextViaWebSocket();
    else await sendTextRest();
  }, [sendTextRest, sendTextViaWebSocket, streamSteps]);

  const onAudioPick = useCallback(
    async (file: File | null) => {
      if (!file) return;
      const sid = sessionId.trim() || "default";
      setError(null);
      setBusy(true);
      setResult(null);
      setSummaryText(null);
      try {
        const fd = new FormData();
        fd.append("audio", file);
        fd.append("session_id", sid);
        fd.append("return_speech", returnSpeech ? "true" : "false");
        const res = await fetch(`${baseUrl}/conversation`, { method: "POST", body: fd });
        const data = (await res.json().catch(() => ({}))) as AgentPayload;
        if (!res.ok) {
          setError(`HTTP ${res.status}: ${JSON.stringify(data)}`);
          return;
        }
        setResult(data);
        await attachAudio(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Request failed");
      } finally {
        setBusy(false);
      }
    },
    [attachAudio, baseUrl, returnSpeech, sessionId],
  );

  const sendBlobWsConversationAudio = useCallback(
    async (blob: Blob, dotExt: string) => {
      const sid = sessionId.trim() || "default";
      setError(null);
      setBusy(true);
      setResult(null);
      setSummaryText(null);
      setWsLog("");

      return new Promise<void>((resolve) => {
        let feedAcc: FeedItem[] = [];
        /** Stable prefix lines (before planner): transcribing → heard transcript. Preserved across plan/tool. */
        let transcriptPrefix: FeedItem[] = [];
        let finalized = false;
        try {
          const ws = new WebSocket(`${wsBase}/ws/conversation_audio`);
          ws.binaryType = "arraybuffer";
          ws.onerror = () => {
            setError("WebSocket audio error (is the API reachable?)");
            setBusy(false);
            resolve();
          };
          ws.onopen = () => {
            ws.send(
              JSON.stringify({
                action: "start",
                session_id: sid,
                language: null,
                return_speech: returnSpeech,
                file_extension: dotExt,
              }),
            );
            void blob.arrayBuffer().then((ab) => {
              ws.send(ab);
              ws.send(JSON.stringify({ action: "finalize" }));
            });
            setWsLog("sent audio via ws");
          };
          ws.onmessage = (ev: MessageEvent) => {
            let payload: AgentPayload;
            try {
              payload = JSON.parse(String(ev.data)) as AgentPayload;
            } catch {
              return;
            }
            const t = typeof payload.type === "string" ? payload.type : "";
            if (t === "ready") return;
            if (t === "stt_started") {
              transcriptPrefix = [{ id: "stt-phase", label: "Transcribing…", tone: "neutral" }];
              setFeed([...transcriptPrefix]);
              return;
            }
            if (t === "stt") {
              const tr = typeof payload.transcript === "string" ? payload.transcript : "";
              const ms =
                typeof payload.stt_elapsed_ms === "number"
                  ? ` · ${payload.stt_elapsed_ms} ms`
                  : "";
              transcriptPrefix = [
                { id: "stt", label: `Heard: ${tr || "(empty)"}${ms}`, tone: "neutral" },
              ];
              setFeed([...transcriptPrefix]);
              return;
            }
            if (t === "plan" && payload.plan && typeof payload.plan === "object") {
              feedAcc = [
                ...transcriptPrefix,
                ...buildActivityFeed({
                  intent: (payload.plan as { intent?: string }).intent,
                  plan: payload.plan as Record<string, unknown>,
                  tool_execution: null,
                }).filter((x) => x.id !== "tool-done"),
              ];
              setFeed(feedAcc);
              return;
            }
            if (t === "tool" && typeof payload.tool_execution === "object") {
              feedAcc = [...feedAcc];
              feedAcc.push(...buildActivityFeed({ plan: {}, tool_execution: payload.tool_execution }));
              setFeed(feedAcc);
              return;
            }
            if (t === "done") {
              finalized = true;
              ws.close();
              setResult(payload as AgentPayload);
              setBusy(false);
              void attachAudio(payload as AgentPayload);
              resolve();
            }
            if (t === "error") {
              const m = typeof payload.message === "string" ? payload.message : "WebSocket error";
              setError(m);
              ws.close();
              setBusy(false);
              resolve();
            }
          };
          ws.onclose = () => {
            if (!finalized) {
              setBusy(false);
              resolve();
            }
          };
        } catch (e) {
          setError(e instanceof Error ? e.message : "WebSocket failed");
          setBusy(false);
          resolve();
        }
      });
    },
    [attachAudio, returnSpeech, sessionId, wsBase],
  );

  const startMic = useCallback(async () => {
    setError(null);
    chunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      setMicVizStream(stream);
      const mr = new MediaRecorder(stream);
      mediaRecorderRef.current = mr;
      mr.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      micStreamRef.current = stream;
      mr.onstop = () => {
        micStreamRef.current?.getTracks().forEach((tr) => tr.stop());
        micStreamRef.current = null;
      };
      mr.start();
      setRecording(true);
    } catch (e) {
      setMicVizStream(null);
      setError(e instanceof Error ? e.message : "Microphone unavailable");
    }
  }, []);

  const stopMicAndSend = useCallback(async () => {
    const mr = mediaRecorderRef.current;
    if (!mr || mr.state !== "recording") return;
    await new Promise<void>((resolve) => {
      mr.onstop = () => {
        micStreamRef.current?.getTracks().forEach((tr) => tr.stop());
        micStreamRef.current = null;
        setMicVizStream(null);
        resolve();
      };
      mr.stop();
      setRecording(false);
      mediaRecorderRef.current = null;
    });
    const chunks = chunksRef.current;
    const blob = new Blob(chunks, { type: chunks[0]?.type || "audio/webm" });
    const dotExt = blob.type.includes("webm") ? ".webm" : ".wav";
    if (useChunkedWsMic) {
      await sendBlobWsConversationAudio(blob, dotExt);
      return;
    }
    const ext = dotExt.replace(/^\./, "");
    const file = new File([blob], `capture.${ext}`, { type: blob.type });
    await onAudioPick(file);
  }, [onAudioPick, sendBlobWsConversationAudio, useChunkedWsMic]);

  const fetchSummary = useCallback(async () => {
    const sid = sessionId.trim() || "default";
    setSummaryBusy(true);
    setSummaryText(null);
    setError(null);
    try {
      const res = await fetch(`${baseUrl}/agent/summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(`Summary HTTP ${res.status}: ${JSON.stringify(data)}`);
        return;
      }
      const s = typeof (data as { summary?: string }).summary === "string" ? (data as { summary: string }).summary : null;
      if (s) setSummaryText(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Summary request failed");
    } finally {
      setSummaryBusy(false);
    }
  }, [baseUrl, sessionId]);

  return (
    <div className="flex min-h-full flex-1 flex-col bg-zinc-50 px-4 py-10 font-sans dark:bg-zinc-950 md:px-8">
      <main className="mx-auto grid w-full max-w-6xl gap-8 lg:grid-cols-[280px_minmax(0,1fr)]">
        {/* Avatar */}
        <section className="flex flex-col items-center gap-4 lg:sticky lg:top-8 lg:self-start">
          <AvatarVoice speaking={speaking} recording={recording} mediaStream={micVizStream} />
          {recording ? (
            <p className="rounded-full bg-rose-100 px-3 py-1 text-xs font-medium text-rose-800 dark:bg-rose-950/60 dark:text-rose-200">
              Recording…
            </p>
          ) : null}
          <LiveKitPanel apiBase={baseUrl} />
        </section>

        <div className="space-y-8">
          <header>
            <p className="text-sm font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Voice call lab
            </p>
            <h1 className="mt-1 text-2xl font-semibold text-zinc-900 dark:text-zinc-50">Healthcare agent</h1>
            <p className="mt-2 max-w-xl text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              Text or microphone → <code className="rounded bg-zinc-100 px-1 font-mono text-xs dark:bg-zinc-800">/process</code> or{" "}
              <code className="rounded bg-zinc-100 px-1 font-mono text-xs dark:bg-zinc-800">/conversation</code>. Stream planner steps via{" "}
              <code className="rounded bg-zinc-100 px-1 font-mono text-xs dark:bg-zinc-800">/ws/agent</code>; chunked mic upload via{" "}
              <code className="rounded bg-zinc-100 px-1 font-mono text-xs dark:bg-zinc-800">/ws/conversation_audio</code>.
            </p>
          </header>

          <div className="grid gap-6 md:grid-cols-2">
            {/* Input */}
            <div className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
              <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400">Session ID</label>
              <input
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
                className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 font-mono text-sm text-zinc-900 outline-none ring-emerald-500/30 focus:ring-2 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                placeholder="web-call-ui"
              />
              <label className="mt-2 flex cursor-pointer items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
                <input
                  type="checkbox"
                  checked={syncSessionToPhone}
                  onChange={(e) => setSyncSessionToPhone(e.target.checked)}
                />
                After <code className="font-mono">identify_user</code>, set session ID to normalized phone (server hint)
              </label>
              <label className="mt-4 block text-xs font-medium text-zinc-500 dark:text-zinc-400">Message</label>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                disabled={busy}
                rows={4}
                className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 outline-none ring-emerald-500/30 focus:ring-2 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                placeholder="e.g. I need to book an appointment Tuesday afternoon."
              />
              <div className="flex flex-wrap gap-3 pt-2">
                <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
                  <input type="checkbox" checked={returnSpeech} onChange={(e) => setReturnSpeech(e.target.checked)} />
                  Return speech (Piper WAV)
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
                  <input type="checkbox" checked={streamSteps} onChange={(e) => setStreamSteps(e.target.checked)} />
                  Stream planner steps via WebSocket
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
                  <input type="checkbox" checked={useChunkedWsMic} onChange={(e) => setUseChunkedWsMic(e.target.checked)} />
                  Mic: send recording via <code className="font-mono">/ws/conversation_audio</code> (chunked binary)
                </label>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={sendText}
                className="mt-3 w-full rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white shadow hover:bg-emerald-700 disabled:opacity-60 dark:bg-emerald-700 dark:hover:bg-emerald-600"
              >
                {busy ? "Working…" : streamSteps ? "Send (WebSocket)" : "Send text"}
              </button>
              <div className="mt-4 border-t border-zinc-200 pt-4 dark:border-zinc-700">
                <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Microphone</p>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    disabled={busy || recording}
                    onClick={startMic}
                    className="flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
                  >
                    Start recording
                  </button>
                  <button
                    type="button"
                    disabled={busy || !recording}
                    onClick={stopMicAndSend}
                    className="flex-1 rounded-lg bg-zinc-800 px-3 py-2 text-sm text-white hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-700"
                  >
                    Stop &amp; send
                  </button>
                </div>
              </div>
              <label className="mt-4 block cursor-pointer text-xs font-medium text-zinc-500 dark:text-zinc-400">
                Or upload audio file
                <input
                  type="file"
                  accept="audio/*,.wav,.webm,.mp3,.ogg"
                  disabled={busy}
                  className="mt-2 block w-full text-sm text-zinc-600 file:mr-3 file:rounded-md file:border-0 file:bg-zinc-100 file:px-3 file:py-1.5 dark:text-zinc-400 dark:file:bg-zinc-800"
                  onChange={(e) => onAudioPick(e.target.files?.[0] ?? null)}
                />
              </label>
            </div>

            {/* Tool activity + summary */}
            <div className="space-y-4 rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300">Tool activity</h2>
              <ul className="space-y-2 text-sm">
                {feed.length === 0 ? (
                  <li className="text-zinc-500 dark:text-zinc-400">No activity yet.</li>
                ) : (
                  feed.map((f, idx) => (
                    <li
                      key={`${idx}-${f.id}`}
                      className={`rounded-lg border px-3 py-2 text-left ${
                        f.tone === "ok"
                          ? "border-emerald-300/70 bg-emerald-50 dark:border-emerald-900/50 dark:bg-emerald-950/30"
                          : f.tone === "warn"
                            ? "border-amber-300/70 bg-amber-50 dark:border-amber-900/50 dark:bg-amber-950/30"
                            : "border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-950/50"
                      }`}
                    >
                      {f.label}
                    </li>
                  ))
                )}
              </ul>

              <div className="border-t border-zinc-200 pt-4 dark:border-zinc-700">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300">Post-call summary</h2>
                <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">Uses server transcript for this session ID.</p>
                <button
                  type="button"
                  disabled={summaryBusy || busy}
                  onClick={fetchSummary}
                  className="mt-3 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-950 dark:hover:bg-zinc-900"
                >
                  {summaryBusy ? "Summarizing…" : "Generate summary"}
                </button>
                {summaryText ? (
                  <div className="mt-4 max-h-48 overflow-auto rounded-lg border border-zinc-100 bg-zinc-50 px-3 py-2 text-xs leading-relaxed text-zinc-800 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200 whitespace-pre-wrap">
                    {summaryText}
                  </div>
                ) : null}
              </div>
            </div>
          </div>

          {(() => {
            const conv = transcriptAndReply(result);
            if (!conv) return null;
            return (
              <section className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300">Conversation</h2>
                <div className="mt-3 space-y-3 text-sm">
                  {conv.user ? (
                    <div>
                      <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400">You</p>
                      <p className="mt-1 rounded-lg bg-zinc-50 px-3 py-2 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
                        {conv.user}
                      </p>
                    </div>
                  ) : null}
                  {conv.assistant ? (
                    <div>
                      <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Assistant</p>
                      <p className="mt-1 rounded-lg bg-emerald-50/90 px-3 py-2 text-emerald-950 dark:bg-emerald-950/40 dark:text-emerald-50">
                        {conv.assistant}
                      </p>
                    </div>
                  ) : null}
                </div>
              </section>
            );
          })()}

          {wsLog ? <p className="text-xs text-zinc-500 dark:text-zinc-400">{wsLog}</p> : null}

          {error ? (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-200">{error}</p>
          ) : null}

          {result ? (
            <details className="rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-zinc-700 dark:text-zinc-200">Raw response JSON</summary>
              <pre className="max-h-96 overflow-auto border-t border-zinc-200 p-4 text-xs text-zinc-800 dark:border-zinc-700 dark:text-zinc-200">
                {JSON.stringify(result, null, 2)}
              </pre>
            </details>
          ) : null}

          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            <Link href="/" className="font-medium text-zinc-900 underline dark:text-zinc-100">
              ← Back to health check
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
