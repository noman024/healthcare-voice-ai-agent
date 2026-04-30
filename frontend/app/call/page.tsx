"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import AvatarVoice from "./AvatarVoice";
import LiveKitPanel, {
  DEFAULT_PUBLIC_LIVEKIT_ROOM_NAME,
  sanitizeLiveKitIdentity,
  sanitizeLiveKitRoomName,
} from "./LiveKitPanel";

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

/** Reassemble base64-encoded byte chunks from the LiveKit worker into a WAV ``Uint8Array``. */
function mergeTtsWavChunks(parts: Map<number, string>): Uint8Array {
  const keys = [...parts.keys()].sort((a, b) => a - b);
  const chunks: Uint8Array[] = [];
  for (const k of keys) {
    const b64 = parts.get(k);
    if (!b64) continue;
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    chunks.push(u8);
  }
  const total = chunks.reduce((s, c) => s + c.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const c of chunks) {
    out.set(c, o);
    o += c.length;
  }
  return out;
}

/** Accept JSON where numeric fields may arrive as strings after relay/parse. */
function coerceVaSeq(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return -1;
}

function coerceVaLast(v: unknown): boolean {
  return v === true || v === "true" || v === 1 || v === "1";
}

function coerceOffsetMs(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return 0;
}

function coerceNonNegInt(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v) && v >= 0) return Math.floor(v);
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n) && n >= 0) return Math.floor(n);
  }
  return null;
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
    const phase = typeof te.phase === "string" ? te.phase : "";
    const ok = te.success === true;
    const errMsg =
      typeof (te.error as Record<string, unknown> | undefined)?.message === "string"
        ? String((te.error as { message?: string }).message)
        : "Failed";
    const toolTag = typeof te.tool === "string" ? te.tool : tool;
    if (phase === "running" && toolTag) {
      out.push({
        id: "tool-running",
        label: summarizeTool(toolTag),
        tone: "neutral",
      });
    } else if (toolTag) {
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
/** After TTS starts, ignore barge-in this long (ms) so speaker→mic bleed does not cancel playback. */
const TTS_BARGE_IN_GRACE_MS = 1_400;
/** Stricter RMS multiplier for barge vs VAD (echo is often below true user interrupt). */
const VAD_BARGE_RMS_MULT = 3.25;

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
  /** When set, browser POSTs lipsync here (e.g. dedicated :8001 service). Defaults to main API (proxy path). */
  const musetalkApiBase = useMemo(
    () => (process.env.NEXT_PUBLIC_MUSETALK_API_URL ?? "").trim().replace(/\/$/, "") || baseUrl,
    [baseUrl],
  );
  const musetalkPortraitUrl = useMemo(() => {
    if ((process.env.NEXT_PUBLIC_MUSETALK_ENABLED ?? "").trim() !== "1") return null;
    return `${musetalkApiBase}/avatar/reference`;
  }, [musetalkApiBase]);
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
  const livekitPublicUrl = useMemo(() => (process.env.NEXT_PUBLIC_LIVEKIT_URL ?? "").trim(), []);

  const lkRoom = useMemo(
    () => sanitizeLiveKitRoomName(conversationId, DEFAULT_PUBLIC_LIVEKIT_ROOM_NAME),
    [conversationId],
  );
  const lkIdentity = useMemo(
    () =>
      sanitizeLiveKitIdentity(
        sessionId.trim(),
        `web-${(conversationId || "session").slice(0, 12)}`,
      ),
    [sessionId, conversationId],
  );

  /** Frozen once per LiveKit attempt so updating session_id (phone sync) does not reconnect RTC. */
  const [liveKitBindings, setLiveKitBindings] = useState<{ room: string; identity: string } | null>(
    null,
  );

  const [chatMode, setChatMode] = useState<"text" | "voice">("text");
  /** User picks in Voice mode; initial default after `/livekit/status` respects ``NEXT_PUBLIC_PREFER_LIVEKIT_VOICE``. */
  const [voiceBackend, setVoiceBackend] = useState<"livekit" | "websocket">("websocket");
  /** ``null`` until first `/livekit/status` in this voice session. */
  const [liveKitStatusOk, setLiveKitStatusOk] = useState<boolean | null>(null);
  const userVoiceBackendPinnedRef = useRef(false);
  const prevChatModeRef = useRef<"text" | "voice">("text");
  const [liveKitSurfaceOn, setLiveKitSurfaceOn] = useState(false);
  /** True once LiveKit publishes the mic track; worker can hear you before assistant events settle. */
  const [liveKitMicLive, setLiveKitMicLive] = useState(false);
  const [voiceSurfaceHint, setVoiceSurfaceHint] = useState<string | null>(null);

  const [syncSessionToPhone, setSyncSessionToPhone] = useState(true);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [useChunkedWsMic, setUseChunkedWsMic] = useState(true);
  const [returnSpeech, setReturnSpeech] = useState(true);
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
  /** ``performance.now()`` ceiling: no TTS barge-in until after this (WebSocket voice echo guard). */
  const ttsBargeInGraceUntilRef = useRef(0);
  /** Invalidate late MuseTalk completions when a new ``attachAudio`` run starts. */
  const busyRef = useRef(false);
  const speakingRef = useRef(false);
  const lastPhoneRef = useRef<string | null>(null);
  const lipsyncBlobUrlRef = useRef<string | null>(null);
  const [lipsyncVideoUrl, setLipsyncVideoUrl] = useState<string | null>(null);
  /** performance.now() when this utterance's TTS timeline started (LiveKit tts_begin / WS play). */
  const [lipsyncSyncStartPerfMs, setLipsyncSyncStartPerfMs] = useState<number | null>(null);
  /** True while companion Piper WAV is playing (REST/WebSocket): video is muted; ignore video `ended` for cleanup. */
  const [lipsyncSuppressVideoEnd, setLipsyncSuppressVideoEnd] = useState(false);
  const ttsWavAssemblyRef = useRef<{ rid: string; parts: Map<number, string> } | null>(null);
  const lipsyncMp4AssemblyRef = useRef<{ rid: string; parts: Map<number, string> } | null>(null);
  /** performance.now() at ``tts_begin`` (or first chunk) keyed by utterance ``rid`` — avoids races back-to-back. */
  const liveKitTtsAnchorByRidRef = useRef<Map<string, number>>(new Map());
  /** Monotonic ordinal per assistant utterance; equals the most recent ``rid``'s value after each ``tts_begin``. */
  const liveKitUtteranceHeadOrdinalRef = useRef(0);
  /** Maps each ``rid`` to the ordinal when that utterance started (staleness = older ordinal than head). */
  const liveKitRidToOrdinalRef = useRef<Map<string, number>>(new Map());
  /** Worker POSTs ``/avatar/lipsync``; browser should not duplicate; WAV kept for fallback on error. */
  const liveKitWorkerLipsyncByRidRef = useRef<Map<string, boolean>>(new Map());
  const liveKitFallbackWavByRidRef = useRef<Map<string, Uint8Array>>(new Map());
  /** True once worker MP4 for this ``rid`` was applied (last WAV may arrive later). */
  const liveKitWorkerLipsyncAppliedRef = useRef<Set<string>>(new Set());
  /** Most recent assistant utterance id from `tts_begin` (or first WAV chunk). */
  const liveKitLatestTtsRidRef = useRef<string | null>(null);
  /** Segmented TTS: worker ``utterance_id`` for timeline sync. */
  const liveKitCurrentUtteranceIdRef = useRef<string | null>(null);
  /** ``performance.now()`` when the current utterance's TTS is first audible in the room (LiveKit assistant VAD); null until then. */
  const liveKitUtteranceAnchorPerfRef = useRef<number | null>(null);
  /** Per-segment audio offset (ms) from utterance start → maps ``rid`` from ``tts_begin``. */
  const liveKitTtsAudioOffsetMsByRidRef = useRef<Map<string, number>>(new Map());
  /** Segmented TTS: segment_index / segment_count for ``rid`` (suppress video `ended` between chunks). */
  const liveKitTtsSegmentByRidRef = useRef<Map<string, { index: number; count: number }>>(new Map());
  const [recording, setRecording] = useState(false);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  const wsVoicePath = chatMode === "voice" && voiceBackend === "websocket";
  const chunkedWsEffective = wsVoicePath || useChunkedWsMic;

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);
  useEffect(() => {
    speakingRef.current = speaking;
  }, [speaking]);

  useEffect(() => {
    if (chatMode !== "voice" || voiceBackend !== "livekit" || !roomReady) {
      setLiveKitBindings(null);
      return;
    }
    setLiveKitBindings((prev) => prev ?? { room: lkRoom, identity: lkIdentity });
  }, [chatMode, voiceBackend, roomReady, lkRoom, lkIdentity]);

  useEffect(() => {
    return () => {
      handsFreeStreamRef.current?.getTracks().forEach((tr) => tr.stop());
    };
  }, []);

  const revokeLipsyncUrl = useCallback(() => {
    const u = lipsyncBlobUrlRef.current;
    if (u) {
      URL.revokeObjectURL(u);
      lipsyncBlobUrlRef.current = null;
    }
    setLipsyncVideoUrl(null);
    setLipsyncSyncStartPerfMs(null);
  }, []);

  const onLipsyncPlaybackEnd = useCallback(() => {
    revokeLipsyncUrl();
    setSpeaking(false);
    speakingRef.current = false;
  }, [revokeLipsyncUrl]);

  useEffect(() => {
    if (chatMode === "text") {
      revokeLipsyncUrl();
      setSpeaking(false);
      speakingRef.current = false;
    }
  }, [chatMode, revokeLipsyncUrl]);

  useEffect(() => {
    return () => {
      const u = lipsyncBlobUrlRef.current;
      if (u) URL.revokeObjectURL(u);
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
      const fallbackLines = transcript
        .filter((line) => line.text.trim())
        .map((line) => `${line.role}: ${line.text.trim()}`);
      const transcriptFallback = fallbackLines.length > 0 ? fallbackLines.join("\n") : undefined;
      const body: Record<string, string | undefined> = {
        session_id: sid,
        conversation_id: conversationId,
      };
      if (transcriptFallback) body.transcript_fallback = transcriptFallback;
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
  }, [baseUrl, sessionId, conversationId, transcript]);

  const applyLiveKitMusetalkVideoBlob = useCallback(
    (blob: Blob, _ttsAnchorPerfMs: number, utteranceRid: string, utteranceOrdinal: number) => {
      if (!utteranceRid) return;
      if (utteranceOrdinal !== liveKitUtteranceHeadOrdinalRef.current) return;
      const musetalkOn = (process.env.NEXT_PUBLIC_MUSETALK_ENABLED ?? "").trim() === "1";
      if (!musetalkOn) {
        setSpeaking(false);
        speakingRef.current = false;
        return;
      }
      if (blob.size < 32) {
        return;
      }
      revokeLipsyncUrl();
      const url = URL.createObjectURL(blob);
      lipsyncBlobUrlRef.current = url;
      const seg = liveKitTtsSegmentByRidRef.current.get(utteranceRid);
      liveKitTtsSegmentByRidRef.current.delete(utteranceRid);
      const suppressEnded = seg != null && seg.count > 1 && seg.index < seg.count - 1;
      setLipsyncSuppressVideoEnd(suppressEnded);
      const offMs = liveKitTtsAudioOffsetMsByRidRef.current.get(utteranceRid) ?? 0;
      const base = liveKitUtteranceAnchorPerfRef.current;
      const useUtteranceTimeline =
        liveKitCurrentUtteranceIdRef.current != null &&
        liveKitTtsAudioOffsetMsByRidRef.current.has(utteranceRid) &&
        base != null;
      const syncAt = useUtteranceTimeline ? base + offMs : performance.now();
      setLipsyncSyncStartPerfMs(syncAt);
      setLipsyncVideoUrl(url);
      setSpeaking(true);
      speakingRef.current = true;
    },
    [revokeLipsyncUrl],
  );

  const onLiveKitAssistantSpeakingStarted = useCallback(() => {
    if (chatMode !== "voice" || voiceBackend !== "livekit") return;
    const ts = performance.now();
    const anchorWasNull = liveKitUtteranceAnchorPerfRef.current == null;
    if (anchorWasNull) {
      liveKitUtteranceAnchorPerfRef.current = ts;
    }
    const rid = liveKitLatestTtsRidRef.current;
    if (!rid) return;
    const ord = liveKitRidToOrdinalRef.current.get(rid) ?? -1;
    if (ord < 0 || ord !== liveKitUtteranceHeadOrdinalRef.current) return;
    const off = liveKitTtsAudioOffsetMsByRidRef.current.get(rid) ?? 0;
    const base = liveKitUtteranceAnchorPerfRef.current;
    if (liveKitCurrentUtteranceIdRef.current != null) {
      if (base != null && anchorWasNull) setLipsyncSyncStartPerfMs(base + off);
    } else if (anchorWasNull) {
      setLipsyncSyncStartPerfMs(ts);
    }
  }, [chatMode, voiceBackend]);

  const runLiveKitMusetalkFromWavBytes = useCallback(
    async (
      wavBytes: Uint8Array,
      ttsAnchorPerfMs: number,
      utteranceRid: string,
      ordinalSnap?: number,
    ) => {
      const musetalkOn = (process.env.NEXT_PUBLIC_MUSETALK_ENABLED ?? "").trim() === "1";
      if (!musetalkOn) {
        setSpeaking(false);
        speakingRef.current = false;
        return;
      }
      const ordAtFetch =
        ordinalSnap !== undefined && ordinalSnap >= 0
          ? ordinalSnap
          : (liveKitRidToOrdinalRef.current.get(utteranceRid) ?? -1);
      const finish = (): void => {
        liveKitRidToOrdinalRef.current.delete(utteranceRid);
        liveKitTtsAudioOffsetMsByRidRef.current.delete(utteranceRid);
        liveKitTtsSegmentByRidRef.current.delete(utteranceRid);
      };
      try {
        const fd = new FormData();
        fd.append("audio", new Blob([wavBytes.slice()], { type: "audio/wav" }), "tts.wav");
        const lr = await fetch(`${musetalkApiBase}/avatar/lipsync`, { method: "POST", body: fd });
        if (ordAtFetch < 0 || ordAtFetch !== liveKitUtteranceHeadOrdinalRef.current) {
          finish();
          return;
        }
        if (lr.ok) {
          const blob = await lr.blob();
          if (ordAtFetch !== liveKitUtteranceHeadOrdinalRef.current) {
            finish();
            return;
          }
          applyLiveKitMusetalkVideoBlob(blob, ttsAnchorPerfMs, utteranceRid, ordAtFetch);
          finish();
        } else {
          finish();
          setSpeaking(false);
          speakingRef.current = false;
        }
      } catch {
        finish();
        setSpeaking(false);
        speakingRef.current = false;
      }
    },
    [musetalkApiBase, applyLiveKitMusetalkVideoBlob],
  );

  const onLiveKitVoiceAgentData = useCallback(
    (msg: Record<string, unknown>) => {
      const kind = typeof msg.kind === "string" ? msg.kind : "";
      if (kind === "tts_begin") {
        const rid = typeof msg.rid === "string" ? msg.rid : "";
        if (!rid) return;
        const utteranceId = typeof msg.utterance_id === "string" ? msg.utterance_id.trim() : "";
        if (utteranceId) {
          if (utteranceId !== liveKitCurrentUtteranceIdRef.current) {
            liveKitCurrentUtteranceIdRef.current = utteranceId;
            liveKitUtteranceHeadOrdinalRef.current += 1;
            liveKitUtteranceAnchorPerfRef.current = null;
          }
        } else {
          liveKitCurrentUtteranceIdRef.current = null;
          liveKitUtteranceHeadOrdinalRef.current += 1;
          liveKitUtteranceAnchorPerfRef.current = null;
        }
        const ord = liveKitUtteranceHeadOrdinalRef.current;
        liveKitLatestTtsRidRef.current = rid;
        liveKitRidToOrdinalRef.current.set(rid, ord);
        liveKitTtsAudioOffsetMsByRidRef.current.set(rid, coerceOffsetMs(msg.audio_offset_ms));
        const segIdx = coerceNonNegInt(msg.segment_index);
        const segCnt = coerceNonNegInt(msg.segment_count);
        if (segIdx != null && segCnt != null && segCnt > 1) {
          liveKitTtsSegmentByRidRef.current.set(rid, { index: segIdx, count: segCnt });
        } else {
          liveKitTtsSegmentByRidRef.current.delete(rid);
        }
        liveKitTtsAnchorByRidRef.current.set(rid, performance.now());
        if (msg.worker_lipsync === true || msg.worker_lipsync === 1 || msg.worker_lipsync === "true") {
          liveKitWorkerLipsyncByRidRef.current.set(rid, true);
        }
        return;
      }
      if (kind === "lipsync_mp4_error") {
        const rid = typeof msg.rid === "string" ? msg.rid : "";
        if (!rid) return;
        liveKitWorkerLipsyncByRidRef.current.delete(rid);
        lipsyncMp4AssemblyRef.current = null;
        liveKitWorkerLipsyncAppliedRef.current.delete(rid);
        const bytes = liveKitFallbackWavByRidRef.current.get(rid);
        liveKitFallbackWavByRidRef.current.delete(rid);
        if (!bytes) {
          liveKitTtsAnchorByRidRef.current.delete(rid);
          liveKitRidToOrdinalRef.current.delete(rid);
          liveKitTtsAudioOffsetMsByRidRef.current.delete(rid);
          liveKitTtsSegmentByRidRef.current.delete(rid);
          return;
        }
        const anchor = liveKitTtsAnchorByRidRef.current.get(rid) ?? performance.now();
        liveKitTtsAnchorByRidRef.current.delete(rid);
        const ordSnap = liveKitRidToOrdinalRef.current.get(rid) ?? -1;
        void runLiveKitMusetalkFromWavBytes(bytes, anchor, rid, ordSnap);
        return;
      }
      if (kind === "lipsync_mp4_chunk") {
        const rid = typeof msg.rid === "string" ? msg.rid : "";
        const seq = coerceVaSeq(msg.seq);
        const last = coerceVaLast(msg.last);
        const b64 = typeof msg.b64 === "string" ? msg.b64 : "";
        if (!rid || seq < 0 || !b64) return;
        let acc = lipsyncMp4AssemblyRef.current;
        if (!acc || acc.rid !== rid) {
          acc = { rid, parts: new Map<number, string>() };
        }
        lipsyncMp4AssemblyRef.current = acc;
        acc.parts.set(seq, b64);
        if (last) {
          lipsyncMp4AssemblyRef.current = null;
          const anchor = liveKitTtsAnchorByRidRef.current.get(rid) ?? performance.now();
          const hadFallback = liveKitFallbackWavByRidRef.current.has(rid);
          const ordForRid = liveKitRidToOrdinalRef.current.get(rid);
          if (ordForRid == null || ordForRid !== liveKitUtteranceHeadOrdinalRef.current) {
            liveKitTtsAnchorByRidRef.current.delete(rid);
            liveKitRidToOrdinalRef.current.delete(rid);
            liveKitFallbackWavByRidRef.current.delete(rid);
            liveKitTtsAudioOffsetMsByRidRef.current.delete(rid);
            liveKitTtsSegmentByRidRef.current.delete(rid);
            return;
          }
          const mp4Bytes = mergeTtsWavChunks(acc.parts);
          const blob = new Blob([Uint8Array.from(mp4Bytes)], { type: "video/mp4" });
          applyLiveKitMusetalkVideoBlob(blob, anchor, rid, ordForRid);
          liveKitTtsAnchorByRidRef.current.delete(rid);
          liveKitRidToOrdinalRef.current.delete(rid);
          liveKitFallbackWavByRidRef.current.delete(rid);
          liveKitTtsAudioOffsetMsByRidRef.current.delete(rid);
          liveKitTtsSegmentByRidRef.current.delete(rid);
          if (hadFallback) {
            liveKitWorkerLipsyncByRidRef.current.delete(rid);
          } else {
            liveKitWorkerLipsyncAppliedRef.current.add(rid);
          }
        }
        return;
      }
      if (kind === "tts_wav_chunk") {
        const rid = typeof msg.rid === "string" ? msg.rid : "";
        const seq = coerceVaSeq(msg.seq);
        const last = coerceVaLast(msg.last);
        const b64 = typeof msg.b64 === "string" ? msg.b64 : "";
        if (!rid || seq < 0 || !b64) return;
        let acc = ttsWavAssemblyRef.current;
        if (!acc || acc.rid !== rid) {
          acc = { rid, parts: new Map<number, string>() };
          liveKitLatestTtsRidRef.current = rid;
          if (!liveKitTtsAnchorByRidRef.current.has(rid)) {
            liveKitTtsAnchorByRidRef.current.set(rid, performance.now());
          }
          if (!liveKitRidToOrdinalRef.current.has(rid)) {
            liveKitUtteranceHeadOrdinalRef.current += 1;
            liveKitRidToOrdinalRef.current.set(rid, liveKitUtteranceHeadOrdinalRef.current);
          }
          if ((process.env.NEXT_PUBLIC_MUSETALK_ENABLED ?? "").trim() === "1") {
            setSpeaking(true);
            speakingRef.current = true;
          }
        }
        ttsWavAssemblyRef.current = acc;
        acc.parts.set(seq, b64);
        if (last) {
          ttsWavAssemblyRef.current = null;
          const bytes = mergeTtsWavChunks(acc.parts);
          const workerLipsync = liveKitWorkerLipsyncByRidRef.current.get(rid) === true;
          const anchor = liveKitTtsAnchorByRidRef.current.get(rid) ?? performance.now();
          const ordSnap = liveKitRidToOrdinalRef.current.get(rid) ?? -1;
          if (workerLipsync) {
            if (liveKitWorkerLipsyncAppliedRef.current.has(rid)) {
              liveKitWorkerLipsyncAppliedRef.current.delete(rid);
              liveKitWorkerLipsyncByRidRef.current.delete(rid);
              liveKitFallbackWavByRidRef.current.delete(rid);
              liveKitTtsAnchorByRidRef.current.delete(rid);
              liveKitRidToOrdinalRef.current.delete(rid);
              liveKitTtsAudioOffsetMsByRidRef.current.delete(rid);
              liveKitTtsSegmentByRidRef.current.delete(rid);
              return;
            }
            liveKitFallbackWavByRidRef.current.set(rid, bytes);
            return;
          }
          liveKitTtsAnchorByRidRef.current.delete(rid);
          liveKitWorkerLipsyncByRidRef.current.delete(rid);
          liveKitFallbackWavByRidRef.current.delete(rid);
          void runLiveKitMusetalkFromWavBytes(bytes, anchor, rid, ordSnap);
        }
        return;
      }
      if (kind === "tool" && msg.tool_execution && typeof msg.tool_execution === "object") {
        const te = msg.tool_execution as Record<string, unknown>;
        setResult((prev) => {
          const base = typeof prev === "object" && prev !== null ? prev : {};
          const next = { ...base, tool_execution: te } as AgentPayload;
          queueMicrotask(() => {
            setStatusLine(buildActivityFeed(next).slice(-4));
          });
          return next;
        });
        return;
      }
      if (kind === "conversation_ended") void fetchSummary();
    },
    [applyLiveKitMusetalkVideoBlob, fetchSummary, runLiveKitMusetalkFromWavBytes],
  );

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

      const musetalkOn = (process.env.NEXT_PUBLIC_MUSETALK_ENABLED ?? "").trim() === "1";

      const playWithSyncClock = async (): Promise<void> => {
        try {
          if (musetalkOn) setLipsyncSuppressVideoEnd(true);
          setSpeaking(true);
          speakingRef.current = true;
          const t0 = performance.now();
          ttsBargeInGraceUntilRef.current = t0 + TTS_BARGE_IN_GRACE_MS;
          setLipsyncSyncStartPerfMs(t0);
          await playWavBase64(b64, playbackAnalyserRef, ttsLifecycleRef);
        } catch {
          /* ignore */
        } finally {
          setLipsyncSuppressVideoEnd(false);
          revokeLipsyncUrl();
          setSpeaking(false);
          speakingRef.current = false;
        }
      };

      if (musetalkOn) {
        try {
          const bin = atob(b64);
          const bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          const fd = new FormData();
          fd.append("audio", new Blob([bytes], { type: "audio/wav" }), "tts.wav");
          const lr = await fetch(`${musetalkApiBase}/avatar/lipsync`, { method: "POST", body: fd });
          if (lr.ok) {
            const blob = await lr.blob();
            if (blob.size >= 32) {
              revokeLipsyncUrl();
              const url = URL.createObjectURL(blob);
              lipsyncBlobUrlRef.current = url;
              setLipsyncVideoUrl(url);
              await playWithSyncClock();
              return;
            }
          }
        } catch {
          /* fall through to audio-only */
        }
        revokeLipsyncUrl();
        setLipsyncSuppressVideoEnd(false);
      }

      await playWithSyncClock();
    },
    [returnSpeech, musetalkApiBase, revokeLipsyncUrl, playbackAnalyserRef, ttsLifecycleRef],
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

  const sendText = sendTextRest;

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
            try {
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
            } catch {
              setError("WebSocket: could not send start frame.");
              setBusy(false);
              try {
                ws.close();
              } catch {
                /* ignore */
              }
              resolve();
              return;
            }
            void blob.arrayBuffer().then(
              (ab) => {
                if (ws.readyState !== WebSocket.OPEN) {
                  setError("WebSocket closed before audio could be sent — check NEXT_PUBLIC_API_URL matches the running API.");
                  setBusy(false);
                  resolve();
                  return;
                }
                try {
                  ws.send(ab);
                  ws.send(JSON.stringify({ action: "finalize" }));
                } catch {
                  setError("WebSocket: send failed (connection dropped).");
                  setBusy(false);
                  resolve();
                }
              },
              () => {
                setError("Could not read recorded audio for upload.");
                setBusy(false);
                try {
                  ws.close();
                } catch {
                  /* ignore */
                }
                resolve();
              },
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
          ws.onclose = (ev) => {
            if (!finalized) {
              if (process.env.NODE_ENV === "development") {
                console.warn("[call] /ws/conversation_audio closed before done", ev.code, ev.reason);
              }
              setError((prev) =>
                prev ??
                (ev.code === 1000
                  ? "Voice WebSocket closed before the assistant reply finished. Confirm NEXT_PUBLIC_API_URL matches your uvicorn host (e.g. http://127.0.0.1:8000)."
                  : `Voice WebSocket closed (${ev.code}${ev.reason ? `: ${ev.reason}` : ""}). Confirm the API is running and browser can reach ${baseUrl}.`),
              );
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
    [attachAudio, returnSpeech, sessionId, wsBase, baseUrl, conversationId, mergeResultIntoTranscript],
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
    if (chunkedWsEffective) {
      await sendBlobWsConversationAudio(blob, dotExt);
      return;
    }
    const ext = dotExt.replace(/^\./, "");
    const file = new File([blob], `capture.${ext}`, { type: blob.type });
    await onAudioPick(file);
  }, [onAudioPick, sendBlobWsConversationAudio, chunkedWsEffective]);

  const stopVoiceSession = useCallback(() => {
    const s = handsFreeStreamRef.current;
    handsFreeStreamRef.current = null;
    s?.getTracks().forEach((tr) => tr.stop());
    setMicVizStream(null);
    setVoiceSessionOn(false);
  }, []);

  useEffect(() => {
    if (chatMode === "text") {
      setLiveKitSurfaceOn(false);
      setLiveKitMicLive(false);
      setVoiceSurfaceHint(null);
      setLiveKitStatusOk(null);
      stopVoiceSession();
      return;
    }
  }, [chatMode, stopVoiceSession]);

  useEffect(() => {
    if (prevChatModeRef.current === "text" && chatMode === "voice") {
      userVoiceBackendPinnedRef.current = false;
      setLiveKitStatusOk(null);
    }
    prevChatModeRef.current = chatMode;
  }, [chatMode]);

  useEffect(() => {
    if (chatMode !== "voice" || !roomReady) return;

    let cancelled = false;
    void (async () => {
      const preferLiveKit =
        (process.env.NEXT_PUBLIC_PREFER_LIVEKIT_VOICE ?? "").trim() === "1";

      if (!livekitPublicUrl) {
        if (!cancelled) {
          setLiveKitStatusOk(false);
          if (!userVoiceBackendPinnedRef.current) {
            setVoiceBackend("websocket");
            setVoiceSurfaceHint(
              "WebSocket voice — set NEXT_PUBLIC_LIVEKIT_URL to enable the LiveKit transport above.",
            );
          }
        }
        return;
      }

      try {
        const res = await fetch(`${baseUrl}/livekit/status`);
        const json = (await res.json()) as { token_service_enabled?: boolean };
        if (cancelled) return;
        const canLk = res.ok && json.token_service_enabled === true;
        setLiveKitStatusOk(canLk);

        if (!userVoiceBackendPinnedRef.current) {
          const next = preferLiveKit && canLk ? "livekit" : "websocket";
          setVoiceBackend(next);
          setVoiceSurfaceHint(
            next === "livekit"
              ? "LiveKit selected — connecting…"
              : canLk
                ? "WebSocket voice — use LiveKit above to exercise the room worker."
                : "WebSocket voice — LiveKit token service unavailable (check API keys /livekit/status).",
          );
        }
      } catch {
        if (!cancelled) {
          setLiveKitStatusOk(false);
          if (!userVoiceBackendPinnedRef.current) {
            setVoiceBackend("websocket");
            setVoiceSurfaceHint("WebSocket voice — could not reach /livekit/status.");
          }
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [chatMode, roomReady, baseUrl, livekitPublicUrl]);

  useEffect(() => {
    if (chatMode !== "voice" || voiceBackend !== "websocket" || !roomReady) return;

    let cancelled = false;

    void (async () => {
      setError(null);
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        if (cancelled) {
          stream.getTracks().forEach((tr) => tr.stop());
          return;
        }
        handsFreeStreamRef.current = stream;
        setMicVizStream(stream);
        setVoiceSessionOn(true);
        setVoiceSurfaceHint("Listening (WebSocket) — pause ~1s after speaking to send.");
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Microphone unavailable");
        }
      }
    })();

    return () => {
      cancelled = true;
      stopVoiceSession();
    };
  }, [chatMode, voiceBackend, roomReady, stopVoiceSession]);

  const onLiveKitConnectionFailed = useCallback((detail: string) => {
    setLiveKitSurfaceOn(false);
    setLiveKitMicLive(false);
    userVoiceBackendPinnedRef.current = true;
    setVoiceSurfaceHint(
      `${detail.length > 100 ? `${detail.slice(0, 100)}…` : detail} — switched to WebSocket.`,
    );
    setVoiceBackend("websocket");
  }, []);

  const onLiveKitConnected = useCallback(() => {
    setLiveKitSurfaceOn(true);
    setVoiceSurfaceHint("LiveKit ready — speaking uses the assistant worker.");
  }, []);

  const onLiveKitMicPublished = useCallback(() => {
    setLiveKitMicLive(true);
  }, []);

  const onLiveKitDisconnected = useCallback(() => {
    setLiveKitSurfaceOn(false);
    setLiveKitMicLive(false);
  }, []);

  useEffect(() => {
    if (voiceBackend !== "livekit") {
      setLiveKitSurfaceOn(false);
      setLiveKitMicLive(false);
    }
  }, [voiceBackend]);

  useEffect(() => {
    if (!wsVoicePath || !voiceSessionOn || !chunkedWsEffective) return undefined;

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
      const bargeLoudEnough = rms > VAD_RMS_THRESHOLD * VAD_BARGE_RMS_MULT;
      const now = performance.now();

      if (
        speakingRef.current &&
        bargeLoudEnough &&
        now >= ttsBargeInGraceUntilRef.current
      ) {
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
  }, [wsVoicePath, voiceSessionOn, chunkedWsEffective, sendBlobWsConversationAudio]);

  const listeningSurface =
    (wsVoicePath && voiceSessionOn) ||
    (chatMode === "voice" &&
      voiceBackend === "livekit" &&
      (liveKitSurfaceOn || liveKitMicLive));

  return (
    <div className="flex min-h-[100dvh] flex-col bg-[#121212] font-sans text-zinc-100">
      {/* Top bar — Meet-like */}
      <header className="flex shrink-0 items-center justify-between border-b border-zinc-800/80 px-4 py-3 md:px-6">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 shadow-lg shadow-emerald-900/30" />
          <div>
            <p className="text-sm font-semibold tracking-tight text-white">Healthcare visit</p>
            <p className="text-[11px] text-zinc-500">Text chat or Voice · Live transcript</p>
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
        {/* Stage — lg:order-2 puts the avatar on the right on wide screens */}
        <section className="flex min-h-[280px] flex-col items-center justify-center border-b border-zinc-800/80 px-4 py-8 lg:order-2 lg:w-[52%] lg:border-b-0 lg:border-l lg:border-zinc-800/80">
          <div className="w-full max-w-md">
            <AvatarVoice
              speaking={speaking}
              recording={recording || listeningSurface}
              mediaStream={micVizStream}
              playbackAnalyserRef={playbackAnalyserRef}
              musetalkPortraitUrl={musetalkPortraitUrl}
              lipsyncVideoUrl={lipsyncVideoUrl}
              lipsyncSuppressVideoEnd={lipsyncSuppressVideoEnd}
              lipsyncSyncStartPerfMs={lipsyncSyncStartPerfMs}
              onLipsyncPlaybackEnd={onLipsyncPlaybackEnd}
            />
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {listeningSurface ? (
                <span className="rounded-full bg-teal-500/20 px-3 py-1 text-xs font-medium text-teal-200">
                  {wsVoicePath && voiceSessionOn
                    ? "Listening (WebSocket)"
                    : liveKitSurfaceOn
                      ? "Listening (LiveKit)"
                      : "Listening (LiveKit mic)"}
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
                Voice troubleshooting
              </summary>
              <div className="space-y-2 border-t border-zinc-800 p-3 text-[11px] text-zinc-500">
                <p>
                  <strong className="text-zinc-400">LiveKit</strong>: run the LiveKit server and{" "}
                  <code className="rounded bg-zinc-950 px-1 font-mono">run_voice_worker.py</code>; room{" "}
                  <code className="font-mono">{roomReady ? lkRoom.slice(0, 24) : "…"}</code>.
                </p>
                <p>
                  Live transcript lines are mirrored from{' '}
                  <strong className="text-zinc-400">room transcription</strong> (user speech + assistant) when Voice
                  uses LiveKit, and from REST/WebSocket when that path runs.
                </p>
                <p>
                  If the status hint ends with{' '}
                  <code className="rounded bg-zinc-950 px-1 font-mono">— switched to WebSocket</code>, something failed
                  the LiveKit connect (timeouts, mic, token) and Voice fell back—the WebSocket path does not reuse the same
                  LiveKit audio.
                </p>
                <p>
                  If you see transcript but no spoken reply, the browser often blocks remote audio until microphone
                  access and explicit playback start; tap the page if the hint mentions autoplay.
                </p>
              </div>
            </details>
          </div>
        </section>

        {/* Transcript + composer — lg:order-1 keeps conversation on the left */}
        <section className="flex min-h-0 flex-1 flex-col bg-[#161616] lg:order-1">
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
                      disabled={
                        busy ||
                        !roomReady ||
                        !livekitPublicUrl ||
                        liveKitStatusOk !== true
                      }
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
                    {voiceSurfaceHint ??
                      (!roomReady ? "Starting session…" : "Voice session starting…")}
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
                      Use <strong className="text-zinc-400">Text chat</strong> to type messages. WebSocket voice auto-sends turns after a pause; you can talk over assistant TTS to interrupt.
                    </p>
                    <p>LiveKit path needs <code className="font-mono text-zinc-400">run_voice_worker.py</code> and matching env on API + worker.</p>
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
                  <input type="checkbox" checked={returnSpeech} onChange={(e) => setReturnSpeech(e.target.checked)} />
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
      </div>

      {livekitPublicUrl && chatMode === "voice" && voiceBackend === "livekit" && roomReady && liveKitBindings ? (
        <LiveKitPanel
          apiBase={baseUrl}
          active
          roomName={liveKitBindings.room}
          identity={liveKitBindings.identity}
          conversationId={conversationId}
          onConnectionFailed={onLiveKitConnectionFailed}
          onConnected={onLiveKitConnected}
          onDisconnected={onLiveKitDisconnected}
          onStatusChange={setVoiceSurfaceHint}
          onMicPublished={onLiveKitMicPublished}
          onTranscriptExchange={appendExchange}
          onVoiceAgentData={onLiveKitVoiceAgentData}
          onAssistantSpeakingStarted={onLiveKitAssistantSpeakingStarted}
        />
      ) : null}
    </div>
  );
}
