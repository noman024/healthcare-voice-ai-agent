"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import AvatarVoice from "./AvatarVoice";
import LiveKitPanel from "./LiveKitPanel";

const defaultApiUrl = "http://localhost:8000";

type FeedTone = "neutral" | "ok" | "warn";
type FeedItem = { id: string; label: string; tone: FeedTone };
type AgentPayload = Record<string, unknown>;

type TranscriptEntry = {
  id: string;
  role: "user" | "assistant";
  text: string;
  at: number;
};

function httpToWsBase(httpUrl: string): string {
  return httpUrl.trim().replace(/\/$/, "").replace(/^http/, "ws");
}

function summarizeTool(tool: unknown): string {
  const t = typeof tool === "string" ? tool : "";
  const labels: Record<string, string> = {
    none: "Thinking…",
    identify_user: "Identifying…",
    fetch_slots: "Loading availability…",
    book_appointment: "Booking…",
    retrieve_appointments: "Loading appointments…",
    cancel_appointment: "Cancelling…",
    modify_appointment: "Updating…",
    end_conversation: "Ending…",
  };
  return labels[t] ?? (t ? `${t}` : "Working…");
}

function buildActivityFeed(result: AgentPayload): FeedItem[] {
  const out: FeedItem[] = [];
  const planRaw = result.plan;
  const plan =
    planRaw && typeof planRaw === "object" ? (planRaw as Record<string, unknown>) : null;
  const intent =
    typeof plan?.intent === "string"
      ? plan.intent
      : typeof result.intent === "string"
        ? result.intent
        : "";
  if (intent) out.push({ id: "intent", label: intent, tone: "neutral" });
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
        : "Failed";
    const toolTag = typeof te.tool === "string" ? te.tool : tool;
    if (toolTag) {
      out.push({
        id: "tool-done",
        label: ok ? `${toolTag} · OK` : `${toolTag} · ${errMsg}`,
        tone: ok ? "ok" : "warn",
      });
    }
  }
  const warn = typeof result.warning === "string" ? result.warning : "";
  if (warn) out.push({ id: "warn", label: warn, tone: "warn" });
  return out;
}

type PlaybackAnalyserRef = MutableRefObject<AnalyserNode | null>;
type TtsLifecycleRef = MutableRefObject<{ stop: () => void } | null>;

/** Hands-free: endpoint speech after ~800ms silence; barge-in after a few loud ticks during TTS. */
const VAD_INTERVAL_MS = 50;
const VAD_RMS_THRESHOLD = 0.036;
const VAD_LOUD_TICKS_TO_START = 2;
const VAD_QUIET_TICKS_TO_END = 16;
const VAD_MIN_UTTERANCE_MS = 450;
const VAD_BARGE_IN_LOUD_TICKS = 3;

function pcmRmsTimeDomain(analyser: AnalyserNode): number {
  const buf = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i]! - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / buf.length);
}

/** Decodes and plays WAV base64; optional lifecycle ``stop`` cuts audio (barge-in). */
async function playWavBase64(
  base64: string,
  playbackAnalyserRef?: PlaybackAnalyserRef,
  lifecycleRef?: TtsLifecycleRef,
): Promise<void> {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const AC =
    typeof window !== "undefined"
      ? window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      : null;
  if (!AC) {
    const blob = new Blob([bytes], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    try {
      await new Promise<void>((resolve, reject) => {
        let finished = false;
        const done = () => {
          if (finished) return;
          finished = true;
          if (lifecycleRef) lifecycleRef.current = null;
          resolve();
        };
        audio.onended = () => done();
        if (lifecycleRef) {
          lifecycleRef.current = {
            stop: () => {
              audio.pause();
              audio.currentTime = 0;
              done();
            },
          };
        }
        void audio.play().catch(reject);
      });
    } finally {
      URL.revokeObjectURL(url);
      if (lifecycleRef) lifecycleRef.current = null;
    }
    return;
  }
  const ac = new AC();
  try {
    const buf = await ac.decodeAudioData(ab.slice(0));
    const src = ac.createBufferSource();
    const an = ac.createAnalyser();
    an.fftSize = 256;
    an.smoothingTimeConstant = 0.45;
    src.buffer = buf;
    src.connect(an);
    an.connect(ac.destination);
    if (playbackAnalyserRef) playbackAnalyserRef.current = an;

    const teardown = () => {
      if (playbackAnalyserRef) playbackAnalyserRef.current = null;
      if (lifecycleRef) lifecycleRef.current = null;
    };

    if (lifecycleRef) {
      lifecycleRef.current = {
        stop: () => {
          try {
            src.stop(0);
          } catch {
            /* already ended */
          }
        },
      };
    }

    await ac.resume();
    await new Promise<void>((resolve) => {
      src.onended = () => {
        teardown();
        resolve();
      };
      src.start(0);
    });
  } finally {
    await ac.close().catch(() => undefined);
    if (playbackAnalyserRef) playbackAnalyserRef.current = null;
    if (lifecycleRef) lifecycleRef.current = null;
  }
}

type CallSummaryPayload = {
  summary: string;
  generated_at?: string;
  appointments?: Array<{
    id: number;
    name: string;
    phone: string;
    date: string;
    time: string;
    status: string;
    created_at: string;
  }>;
  user_preferences?: string[];
  phone?: string | null;
  cost_hints?: Record<string, unknown>;
  conversation_id?: string;
};

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export default function CallPage() {
  const baseUrl = (process.env.NEXT_PUBLIC_API_URL ?? defaultApiUrl).replace(/\/$/, "");
  const wsBase = httpToWsBase(baseUrl);

  /** Stable per-tab room id; assigned only after mount to avoid SSR/client hydration mismatches. */
  const [conversationId, setConversationId] = useState("");
  const [sessionId, setSessionId] = useState("");

  useEffect(() => {
    const id = newId();
    setConversationId(id);
    setSessionId(id);
  }, []);

  const roomReady = conversationId.length > 0;
  const [syncSessionToPhone, setSyncSessionToPhone] = useState(true);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [streamSteps, setStreamSteps] = useState(true);
  const [useChunkedWsMic, setUseChunkedWsMic] = useState(true);
  const [returnSpeech, setReturnSpeech] = useState(true);
  const [handsFreeMic, setHandsFreeMic] = useState(true);
  const [voiceSessionOn, setVoiceSessionOn] = useState(false);
  const [micVizStream, setMicVizStream] = useState<MediaStream | null>(null);
  const [result, setResult] = useState<AgentPayload | null>(null);
  const [statusLine, setStatusLine] = useState<FeedItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [callSummary, setCallSummary] = useState<CallSummaryPayload | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [liveCaption, setLiveCaption] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const playbackAnalyserRef = useRef<AnalyserNode | null>(null);
  const handsFreeStreamRef = useRef<MediaStream | null>(null);
  const ttsLifecycleRef = useRef<{ stop: () => void } | null>(null);
  const busyRef = useRef(false);
  const speakingRef = useRef(false);
  const lastPhoneRef = useRef<string | null>(null);
  const [recording, setRecording] = useState(false);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);
  useEffect(() => {
    speakingRef.current = speaking;
  }, [speaking]);

  useEffect(() => {
    return () => {
      handsFreeStreamRef.current?.getTracks().forEach((tr) => tr.stop());
    };
  }, []);

  const appendExchange = useCallback((user: string | null, assistant: string | null) => {
    const t = Date.now();
    setTranscript((prev) => {
      const next = [...prev];
      if (user?.trim()) {
        next.push({ id: newId(), role: "user", text: user.trim(), at: t });
      }
      if (assistant?.trim()) {
        next.push({ id: newId(), role: "assistant", text: assistant.trim(), at: t + 1 });
      }
      return next;
    });
  }, []);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [transcript, liveCaption]);

  const fetchSummary = useCallback(async () => {
    const sid = sessionId.trim() || "default";
    setSummaryBusy(true);
    setCallSummary(null);
    setError(null);
    try {
      const body: Record<string, string | undefined> = {
        session_id: sid,
        conversation_id: conversationId,
      };
      const lp = lastPhoneRef.current?.trim();
      if (lp) body.phone = lp;
      const res = await fetch(`${baseUrl}/agent/summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await res.json().catch(() => ({}))) as CallSummaryPayload & { detail?: string };
      if (!res.ok) {
        setError(`Summary HTTP ${res.status}: ${JSON.stringify(data)}`);
        return;
      }
      if (typeof data.summary === "string") setCallSummary(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Summary request failed");
    } finally {
      setSummaryBusy(false);
    }
  }, [baseUrl, sessionId, conversationId]);

  useEffect(() => {
    const feedItems = result ? buildActivityFeed(result) : [];
    setStatusLine(feedItems.slice(-4));
  }, [result]);

  useEffect(() => {
    if (!result || typeof result.tool_execution !== "object" || result.tool_execution === null) return;
    const te = result.tool_execution as Record<string, unknown>;
    if (te.success !== true) return;
    const data = te.data as Record<string, unknown> | undefined;
    if (data) {
      if (typeof data.phone === "string" && data.phone.trim()) lastPhoneRef.current = data.phone.trim();
      const appt = data.appointment as { phone?: string } | undefined;
      if (appt && typeof appt.phone === "string" && appt.phone.trim()) lastPhoneRef.current = appt.phone.trim();
    }
    const tool = typeof te.tool === "string" ? te.tool : "";
    if (tool === "end_conversation") void fetchSummary();
  }, [result, fetchSummary]);

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
        speakingRef.current = true;
        await playWavBase64(b64, playbackAnalyserRef, ttsLifecycleRef);
      } catch {
        /* ignore */
      } finally {
        setSpeaking(false);
        speakingRef.current = false;
      }
    },
    [returnSpeech],
  );

  const mergeResultIntoTranscript = useCallback(
    (data: AgentPayload) => {
      const tr = typeof data.transcript === "string" ? data.transcript.trim() : "";
      const asst = typeof data.final_response === "string" ? data.final_response.trim() : "";
      appendExchange(tr || null, asst || null);
    },
    [appendExchange],
  );

  const sendTextRest = useCallback(async () => {
    const msg = text.trim();
    const sid = sessionId.trim() || "default";
    if (!msg) return;
    setError(null);
    setBusy(true);
    setResult(null);
    setCallSummary(null);
    setLiveCaption(null);
    try {
      const res = await fetch(`${baseUrl}/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          session_id: sid,
          conversation_id: conversationId,
          return_speech: returnSpeech,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as AgentPayload;
      if (!res.ok) {
        setError(`HTTP ${res.status}: ${JSON.stringify(data)}`);
        return;
      }
      setResult(data);
      mergeResultIntoTranscript(data);
      await attachAudio(data);
      setText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }, [attachAudio, baseUrl, returnSpeech, sessionId, text, conversationId, mergeResultIntoTranscript]);

  const sendTextViaWebSocket = useCallback(async () => {
    const msg = text.trim();
    const sid = sessionId.trim() || "default";
    if (!msg) return;
    setError(null);
    setBusy(true);
    setResult(null);
    setCallSummary(null);
    setLiveCaption(null);

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
          ws.send(
            JSON.stringify({ action: "turn", message: msg, session_id: sid, conversation_id: conversationId }),
          );
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
            setStatusLine(feedAcc);
          }
          if (t === "tool" && typeof payload.tool_execution === "object") {
            feedAcc = [...feedAcc];
            feedAcc.push(...buildActivityFeed({ plan: {}, tool_execution: payload.tool_execution }));
            setStatusLine(feedAcc.slice(-4));
          }
          if (t === "done") {
            ws.close();
            setResult(payload as AgentPayload);
            const d = payload as AgentPayload;
            mergeResultIntoTranscript({ ...d, transcript: msg });
            setBusy(false);
            void attachAudio(payload as AgentPayload);
            setText("");
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
  }, [attachAudio, wsBase, sessionId, text, conversationId, mergeResultIntoTranscript]);

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
      setCallSummary(null);
      setLiveCaption(null);
      try {
        const fd = new FormData();
        fd.append("audio", file);
        fd.append("session_id", sid);
        fd.append("conversation_id", conversationId);
        fd.append("return_speech", returnSpeech ? "true" : "false");
        const res = await fetch(`${baseUrl}/conversation`, { method: "POST", body: fd });
        const data = (await res.json().catch(() => ({}))) as AgentPayload;
        if (!res.ok) {
          setError(`HTTP ${res.status}: ${JSON.stringify(data)}`);
          return;
        }
        setResult(data);
        mergeResultIntoTranscript(data);
        await attachAudio(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Request failed");
      } finally {
        setBusy(false);
      }
    },
    [attachAudio, baseUrl, returnSpeech, sessionId, conversationId, mergeResultIntoTranscript],
  );

  const sendBlobWsConversationAudio = useCallback(
    async (blob: Blob, dotExt: string) => {
      const sid = sessionId.trim() || "default";
      setError(null);
      setBusy(true);
      setResult(null);
      setCallSummary(null);
      setLiveCaption(null);

      return new Promise<void>((resolve) => {
        let feedAcc: FeedItem[] = [];
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
                conversation_id: conversationId,
                language: null,
                return_speech: returnSpeech,
                file_extension: dotExt,
              }),
            );
            void blob.arrayBuffer().then((ab) => {
              ws.send(ab);
              ws.send(JSON.stringify({ action: "finalize" }));
            });
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
              setLiveCaption("Listening…");
              transcriptPrefix = [{ id: "stt-phase", label: "Transcribing…", tone: "neutral" }];
              setStatusLine([...transcriptPrefix]);
              return;
            }
            if (t === "stt") {
              const tr = typeof payload.transcript === "string" ? payload.transcript : "";
              setLiveCaption(tr || "…");
              transcriptPrefix = [
                { id: "stt", label: tr || "(empty)", tone: "neutral" },
              ];
              setStatusLine([...transcriptPrefix]);
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
              setStatusLine(feedAcc.slice(-4));
              return;
            }
            if (t === "tool" && typeof payload.tool_execution === "object") {
              feedAcc = [...feedAcc];
              feedAcc.push(...buildActivityFeed({ plan: {}, tool_execution: payload.tool_execution }));
              setStatusLine(feedAcc.slice(-4));
              return;
            }
            if (t === "done") {
              finalized = true;
              ws.close();
              setResult(payload as AgentPayload);
              setBusy(false);
              setLiveCaption(null);
              mergeResultIntoTranscript(payload as AgentPayload);
              void attachAudio(payload as AgentPayload);
              resolve();
            }
            if (t === "error") {
              const m = typeof payload.message === "string" ? payload.message : "WebSocket error";
              setError(m);
              ws.close();
              setBusy(false);
              setLiveCaption(null);
              resolve();
            }
          };
          ws.onclose = () => {
            if (!finalized) {
              setBusy(false);
              setLiveCaption(null);
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
    [attachAudio, returnSpeech, sessionId, wsBase, conversationId, mergeResultIntoTranscript],
  );

  const startMic = useCallback(async () => {
    setError(null);
    if (voiceSessionOn) {
      setError("Turn off hands-free voice chat first.");
      return;
    }
    chunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
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
  }, [voiceSessionOn]);

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

  const stopVoiceSession = useCallback(() => {
    const s = handsFreeStreamRef.current;
    handsFreeStreamRef.current = null;
    s?.getTracks().forEach((tr) => tr.stop());
    setMicVizStream(null);
    setVoiceSessionOn(false);
  }, []);

  const startVoiceSession = useCallback(async () => {
    if (!handsFreeMic) return;
    if (recording) {
      setError("Stop push-to-talk recording first.");
      return;
    }
    if (!useChunkedWsMic) {
      setError('Enable “Mic via WebSocket audio” in Advanced for hands-free voice.');
      return;
    }
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      handsFreeStreamRef.current = stream;
      setMicVizStream(stream);
      setVoiceSessionOn(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Microphone unavailable");
    }
  }, [handsFreeMic, useChunkedWsMic, recording]);

  useEffect(() => {
    if (!handsFreeMic && voiceSessionOn) stopVoiceSession();
  }, [handsFreeMic, voiceSessionOn, stopVoiceSession]);

  useEffect(() => {
    if (!useChunkedWsMic && voiceSessionOn) stopVoiceSession();
  }, [useChunkedWsMic, voiceSessionOn, stopVoiceSession]);

  useEffect(() => {
    if (!handsFreeMic || !voiceSessionOn || !useChunkedWsMic) return undefined;

    const stream = handsFreeStreamRef.current;
    if (!stream) return undefined;

    let cancelled = false;
    const ac = new AudioContext();
    const analyser = ac.createAnalyser();
    analyser.fftSize = 1024;
    const src = ac.createMediaStreamSource(stream);
    src.connect(analyser);

    let utterLoud = 0;
    let utterQuiet = 0;
    let bargeLoud = 0;
    let inSpeech = false;
    let speechStartMs = 0;
    let segmentRecorder: MediaRecorder | null = null;
    const segmentChunks: Blob[] = [];

    const stopRecorderAndBlob = (): Promise<Blob | null> =>
      new Promise((resolve) => {
        const rec = segmentRecorder;
        if (!rec || rec.state === "inactive") {
          segmentRecorder = null;
          resolve(null);
          return;
        }
        rec.onstop = () => {
          segmentRecorder = null;
          const chunks = [...segmentChunks];
          segmentChunks.length = 0;
          const blob =
            chunks.length > 0
              ? new Blob(chunks, { type: chunks[0]?.type || "audio/webm" })
              : null;
          resolve(blob);
        };
        rec.stop();
      });

    void ac.resume();

    const intervalId = window.setInterval(() => {
      if (cancelled) return;
      const rms = pcmRmsTimeDomain(analyser);
      const loud = rms > VAD_RMS_THRESHOLD;

      if (speakingRef.current && loud) {
        bargeLoud += 1;
        if (bargeLoud >= VAD_BARGE_IN_LOUD_TICKS) {
          ttsLifecycleRef.current?.stop();
          bargeLoud = 0;
        }
      } else {
        bargeLoud = 0;
      }

      if (busyRef.current) return;

      if (loud) {
        utterQuiet = 0;
        utterLoud += 1;
        if (!inSpeech && utterLoud >= VAD_LOUD_TICKS_TO_START) {
          inSpeech = true;
          utterLoud = 0;
          speechStartMs = Date.now();
          segmentChunks.length = 0;
          try {
            segmentRecorder = new MediaRecorder(stream);
            segmentRecorder.ondataavailable = (e) => {
              if (e.data.size) segmentChunks.push(e.data);
            };
            segmentRecorder.start(250);
          } catch {
            inSpeech = false;
          }
        }
      } else {
        utterLoud = 0;
        if (inSpeech) {
          utterQuiet += 1;
          if (utterQuiet >= VAD_QUIET_TICKS_TO_END) {
            inSpeech = false;
            utterQuiet = 0;
            const utterStart = speechStartMs;
            void (async () => {
              const blob = await stopRecorderAndBlob();
              if (cancelled || !blob || blob.size < 120) return;
              if (Date.now() - utterStart < VAD_MIN_UTTERANCE_MS) return;
              const dotExt = blob.type.includes("webm") ? ".webm" : ".wav";
              await sendBlobWsConversationAudio(blob, dotExt);
            })();
          }
        }
      }
    }, VAD_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      src.disconnect();
      void ac.close();
      if (segmentRecorder && segmentRecorder.state !== "inactive") {
        try {
          segmentRecorder.stop();
        } catch {
          /* ignore */
        }
      }
    };
  }, [handsFreeMic, voiceSessionOn, useChunkedWsMic, sendBlobWsConversationAudio]);

  return (
    <div className="flex min-h-[100dvh] flex-col bg-[#121212] font-sans text-zinc-100">
      {/* Top bar — Meet-like */}
      <header className="flex shrink-0 items-center justify-between border-b border-zinc-800/80 px-4 py-3 md:px-6">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 shadow-lg shadow-emerald-900/30" />
          <div>
            <p className="text-sm font-semibold tracking-tight text-white">Healthcare visit</p>
            <p className="text-[11px] text-zinc-500">Voice &amp; text · Live transcript</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
              busy ? "bg-amber-500/20 text-amber-200" : "bg-zinc-800 text-zinc-400"
            }`}
          >
            {busy ? "Working" : !roomReady ? "Starting" : "Ready"}
          </span>
          <Link
            href="/"
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800"
          >
            Exit
          </Link>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* Stage */}
        <section className="flex min-h-[280px] flex-col items-center justify-center border-b border-zinc-800/80 px-4 py-8 lg:w-[52%] lg:border-b-0 lg:border-r">
          <div className="w-full max-w-md">
            <AvatarVoice
              speaking={speaking}
  recording={recording || (handsFreeMic && voiceSessionOn)}
              mediaStream={micVizStream}
              playbackAnalyserRef={playbackAnalyserRef}
            />
            <div className="mt-4 flex justify-center gap-2">
              {handsFreeMic && voiceSessionOn ? (
                <span className="rounded-full bg-teal-500/20 px-3 py-1 text-xs font-medium text-teal-200">
                  Listening (hands-free)
                </span>
              ) : null}
              {recording ? (
                <span className="rounded-full bg-red-500/20 px-3 py-1 text-xs font-medium text-red-300">
                  Recording
                </span>
              ) : null}
              {speaking ? (
                <span className="rounded-full bg-emerald-500/20 px-3 py-1 text-xs font-medium text-emerald-300">
                  Assistant speaking
                </span>
              ) : null}
            </div>
          </div>

          <div className="mt-8 w-full max-w-lg space-y-2">
            {statusLine.length > 0 ? (
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 px-3 py-2">
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Status</p>
                <ul className="space-y-1 text-xs text-zinc-300">
                  {statusLine.map((f, idx) => (
                    <li
                      key={`${idx}-${f.id}`}
                      className={
                        f.tone === "ok"
                          ? "text-emerald-400/90"
                          : f.tone === "warn"
                            ? "text-amber-300/90"
                            : "text-zinc-400"
                      }
                    >
                      {f.label}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <details className="rounded-xl border border-zinc-800 bg-zinc-900/30 text-xs text-zinc-400">
              <summary className="cursor-pointer px-3 py-2 font-medium text-zinc-300">
                LiveKit voice (optional WebRTC)
              </summary>
              <div className="border-t border-zinc-800 p-3">
                <p className="mb-2 text-[11px] text-zinc-500">
                  Not a video bridge: browser mic goes to the LiveKit server; use this when running the
                  Python worker. Otherwise use voice chat below (WebSocket + VAD).
                </p>
                <LiveKitPanel apiBase={baseUrl} />
              </div>
            </details>
          </div>
        </section>

        {/* Transcript + composer */}
        <section className="flex min-h-0 flex-1 flex-col bg-[#161616]">
          <div className="flex shrink-0 items-center justify-between border-b border-zinc-800 px-4 py-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Live transcript</h2>
            <button
              type="button"
              disabled={summaryBusy || busy || !roomReady}
              onClick={fetchSummary}
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

          {/* Composer */}
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
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy || !roomReady}
                  onClick={sendText}
                  className="rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
                >
                  Send
                </button>
                {handsFreeMic ? (
                  <button
                    type="button"
                    disabled={busy || !roomReady || recording}
                    onClick={() => (voiceSessionOn ? stopVoiceSession() : void startVoiceSession())}
                    className={
                      voiceSessionOn
                        ? "rounded-xl bg-rose-700/90 px-4 py-2.5 text-sm font-medium text-white hover:bg-rose-600 disabled:opacity-40"
                        : "rounded-xl bg-teal-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-teal-500 disabled:opacity-40"
                    }
                    title="Hands-free: speak naturally; pause ~1s to send. Interrupt assistant by speaking over TTS."
                  >
                    {voiceSessionOn ? "Stop voice" : "Voice chat"}
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      disabled={busy || recording || voiceSessionOn || !roomReady}
                      onClick={startMic}
                      className="rounded-xl border border-zinc-600 px-4 py-2.5 text-sm text-zinc-200 hover:bg-zinc-800 disabled:opacity-40"
                      title="Push-to-talk: record"
                    >
                      Mic
                    </button>
                    <button
                      type="button"
                      disabled={busy || !recording}
                      onClick={() => void stopMicAndSend()}
                      className="rounded-xl bg-zinc-100 px-4 py-2.5 text-sm font-medium text-zinc-900 hover:bg-white disabled:opacity-40"
                      title="Stop and send"
                    >
                      Stop
                    </button>
                  </>
                )}
              </div>
              {handsFreeMic ? (
                <p className="mx-auto mt-2 max-w-2xl text-[11px] text-zinc-500">
                  Voice chat uses browser echo cancellation / noise suppression, client VAD (silence
                  sends your turn), and you can talk over the assistant to cut off TTS. For full
                  server-side turn-taking, use LiveKit + the worker (panel on the left).
                </p>
              ) : null}
            </div>

            <details className="mx-auto mt-3 max-w-2xl text-[11px] text-zinc-500">
              <summary className="cursor-pointer text-zinc-400">Advanced</summary>
              <div className="mt-2 space-y-2 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={handsFreeMic}
                    onChange={(e) => setHandsFreeMic(e.target.checked)}
                  />
                  Hands-free voice (VAD auto-send, interrupt TTS) — off = push-to-talk Mic/Stop
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={syncSessionToPhone}
                    onChange={(e) => setSyncSessionToPhone(e.target.checked)}
                  />
                  After identify, use phone as session ID (booking)
                </label>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={returnSpeech} onChange={(e) => setReturnSpeech(e.target.checked)} />
                  Spoken replies (TTS)
                </label>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={streamSteps} onChange={(e) => setStreamSteps(e.target.checked)} />
                  Stream planner (WebSocket) for text send
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={useChunkedWsMic}
                    onChange={(e) => setUseChunkedWsMic(e.target.checked)}
                  />
                  Mic via WebSocket audio (recommended)
                </label>
                <p className="font-mono text-[10px] text-zinc-600">
                  Room: {roomReady ? `${conversationId.slice(0, 8)}…` : "…"} · Agent session:{" "}
                  {sessionId || "…"}
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
      </div>
    </div>
  );
}
