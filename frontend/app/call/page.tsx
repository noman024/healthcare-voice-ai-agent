"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  TTS_BARGE_IN_GRACE_MS,
  VAD_BARGE_IN_LOUD_TICKS,
  VAD_BARGE_RMS_MULT,
  VAD_INTERVAL_MS,
  VAD_LOUD_TICKS_TO_START,
  VAD_MIN_UTTERANCE_MS,
  VAD_QUIET_TICKS_TO_END,
  VAD_RMS_THRESHOLD,
  pcmRmsTimeDomain,
  playWavBase64,
} from "./audioPlayback";
import { CallSessionStatusBar } from "./components/CallSessionStatusBar";
import { CallStageSection } from "./components/CallStageSection";
import { CallTopHeader } from "./components/CallTopHeader";
import { CallTranscriptColumn } from "./components/CallTranscriptColumn";
import type { AgentPayload, CallSummaryPayload, FeedItem, TranscriptEntry } from "./callTypes";
import {
  buildActivityFeed,
  coerceNonNegInt,
  coerceOffsetMs,
  coerceVaLast,
  coerceVaSeq,
  httpToWsBase,
  resolveClientApiBase,
  mergeTtsWavChunks,
  newId,
  suggestedSessionIdFromResult,
  toolExecutionStatusBanner,
} from "./callUtils";
import { useConversationIds } from "./hooks/useConversationIds";
import { useLiveKitRoomIdentity } from "./hooks/useLiveKitRoomIdentity";
import LiveKitPanel from "./LiveKitPanel";
import { ClientDemoShareBar } from "./ClientDemoShareBar";

export default function CallPage() {
  const baseUrl = resolveClientApiBase();
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

  const { conversationId, sessionId, setSessionId, roomReady } = useConversationIds();
  const { lkRoom, lkIdentity } = useLiveKitRoomIdentity(conversationId, sessionId);

  const livekitPublicUrl = useMemo(() => (process.env.NEXT_PUBLIC_LIVEKIT_URL ?? "").trim(), []);

  /** Advance MuseTalk seek vs room TTS (ms): RTC audio is often slightly ahead of ``<video>`` decode/play. */
  const liveKitLipsyncBiasMs = useMemo(() => {
    const raw = (process.env.NEXT_PUBLIC_LIVEKIT_LIPSYNC_BIAS_MS ?? "90").trim();
    const n = Number(raw);
    if (!Number.isFinite(n)) return 90;
    return Math.max(0, Math.min(400, n));
  }, []);

  const liveKitSyncPerfMs = useCallback(
    (basePerf: number | null, offsetMs: number): number => {
      const t = basePerf != null ? basePerf + offsetMs : performance.now();
      return t - liveKitLipsyncBiasMs;
    },
    [liveKitLipsyncBiasMs],
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
  const liveKitTtsSegmentByRidRef = useRef<Map<string, { index: number; count: number }>>(
    new Map(),
  );
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
      setLipsyncSyncStartPerfMs(liveKitSyncPerfMs(base, offMs));
      setLipsyncVideoUrl(url);
      setSpeaking(true);
      speakingRef.current = true;
    },
    [revokeLipsyncUrl, liveKitSyncPerfMs],
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
    if (!anchorWasNull) return;
    const off = liveKitTtsAudioOffsetMsByRidRef.current.get(rid) ?? 0;
    const base = liveKitUtteranceAnchorPerfRef.current;
    if (base == null) return;
    setLipsyncSyncStartPerfMs(liveKitSyncPerfMs(base, off));
  }, [chatMode, voiceBackend, liveKitSyncPerfMs]);

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
        if (
          msg.worker_lipsync === true ||
          msg.worker_lipsync === 1 ||
          msg.worker_lipsync === "true"
        ) {
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
        const tName = typeof te.tool === "string" ? te.tool.trim() : "";
        const phase = typeof te.phase === "string" ? te.phase : "";
        setResult((prev) => {
          const base = typeof prev === "object" && prev !== null ? prev : {};
          const plan =
            tName && tName !== "none"
              ? {
                  intent: phase === "running" ? "Voice assistant" : "Voice visit",
                  tool: tName,
                  arguments: {} as Record<string, unknown>,
                }
              : undefined;
          return {
            ...base,
            tool_execution: te,
            ...(plan ? { plan } : {}),
          } as AgentPayload;
        });
        return;
      }
      if (kind === "conversation_ended") void fetchSummary();
    },
    [applyLiveKitMusetalkVideoBlob, fetchSummary, runLiveKitMusetalkFromWavBytes],
  );

  useEffect(() => {
    const feedItems = result ? buildActivityFeed(result) : [];
    setStatusLine(feedItems.slice(-8));
  }, [result]);

  useEffect(() => {
    if (!result || typeof result.tool_execution !== "object" || result.tool_execution === null)
      return;
    const te = result.tool_execution as Record<string, unknown>;
    if (te.success !== true) return;
    const data = te.data as Record<string, unknown> | undefined;
    if (data) {
      if (typeof data.phone === "string" && data.phone.trim())
        lastPhoneRef.current = data.phone.trim();
      const appt = data.appointment as { phone?: string } | undefined;
      if (appt && typeof appt.phone === "string" && appt.phone.trim())
        lastPhoneRef.current = appt.phone.trim();
    }
    const tool = typeof te.tool === "string" ? te.tool : "";
    if (tool === "end_conversation") void fetchSummary();
  }, [result, fetchSummary]);

  /** Same phone session for REST, WebSocket, and LiveKit (worker only sends ``tool_execution`` for tools). */
  useEffect(() => {
    if (!syncSessionToPhone || !result) return;
    const next = suggestedSessionIdFromResult(result);
    if (next && next !== sessionId.trim()) setSessionId(next);
  }, [result, syncSessionToPhone, sessionId, setSessionId]);

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
  }, [
    attachAudio,
    baseUrl,
    returnSpeech,
    sessionId,
    text,
    conversationId,
    mergeResultIntoTranscript,
  ]);

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
                  setError(
                    "WebSocket closed before audio could be sent — check NEXT_PUBLIC_API_URL matches the running API.",
                  );
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
              transcriptPrefix = [{ id: "stt", label: tr || "(empty)", tone: "neutral" }];
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
              setStatusLine(feedAcc.slice(-8));
              return;
            }
            if (t === "tool" && typeof payload.tool_execution === "object") {
              feedAcc = [...feedAcc];
              feedAcc.push(
                ...buildActivityFeed({ plan: {}, tool_execution: payload.tool_execution }),
              );
              setStatusLine(feedAcc.slice(-8));
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
                console.warn(
                  "[call] /ws/conversation_audio closed before done",
                  ev.code,
                  ev.reason,
                );
              }
              setError(
                (prev) =>
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
    [
      attachAudio,
      returnSpeech,
      sessionId,
      wsBase,
      baseUrl,
      conversationId,
      mergeResultIntoTranscript,
    ],
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
      const preferLiveKit = (process.env.NEXT_PUBLIC_PREFER_LIVEKIT_VOICE ?? "").trim() === "1";

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
            chunks.length > 0 ? new Blob(chunks, { type: chunks[0]?.type || "audio/webm" }) : null;
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

      if (speakingRef.current && bargeLoudEnough && now >= ttsBargeInGraceUntilRef.current) {
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
    (chatMode === "voice" && voiceBackend === "livekit" && (liveKitSurfaceOn || liveKitMicLive));

  const transportLabel = useMemo(() => {
    if (chatMode === "text") return "Text";
    return voiceBackend === "livekit" ? "Voice · LiveKit" : "Voice · WebSocket";
  }, [chatMode, voiceBackend]);

  const headerToolBanner = useMemo(
    () => toolExecutionStatusBanner(result?.tool_execution),
    [result],
  );

  return (
    <div className="flex min-h-[100dvh] flex-col bg-[#121212] font-sans text-zinc-100">
      <CallTopHeader busy={busy} roomReady={roomReady} />

      <ClientDemoShareBar />

      <CallSessionStatusBar
        transportLabel={transportLabel}
        conversationId={conversationId}
        roomReady={roomReady}
        sessionId={sessionId}
        headerToolBanner={headerToolBanner}
      />

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <CallStageSection
          speaking={speaking}
          recording={recording}
          listeningSurface={listeningSurface}
          wsVoicePath={wsVoicePath}
          voiceSessionOn={voiceSessionOn}
          liveKitSurfaceOn={liveKitSurfaceOn}
          liveKitMicLive={liveKitMicLive}
          micVizStream={micVizStream}
          playbackAnalyserRef={playbackAnalyserRef}
          musetalkPortraitUrl={musetalkPortraitUrl}
          lipsyncVideoUrl={lipsyncVideoUrl}
          lipsyncSuppressVideoEnd={lipsyncSuppressVideoEnd}
          lipsyncSyncStartPerfMs={lipsyncSyncStartPerfMs}
          onLipsyncPlaybackEnd={onLipsyncPlaybackEnd}
          statusLine={statusLine}
          busy={busy}
          roomReady={roomReady}
          lkRoom={lkRoom}
        />

        <CallTranscriptColumn
          transcript={transcript}
          liveCaption={liveCaption}
          transcriptEndRef={transcriptEndRef}
          callSummary={callSummary}
          summaryBusy={summaryBusy}
          busy={busy}
          roomReady={roomReady}
          error={error}
          chatMode={chatMode}
          setChatMode={setChatMode}
          text={text}
          setText={setText}
          sendText={sendText}
          voiceBackend={voiceBackend}
          setVoiceBackend={setVoiceBackend}
          livekitPublicUrl={livekitPublicUrl}
          liveKitStatusOk={liveKitStatusOk}
          voiceSurfaceHint={voiceSurfaceHint}
          setVoiceSurfaceHint={setVoiceSurfaceHint}
          userVoiceBackendPinnedRef={userVoiceBackendPinnedRef}
          syncSessionToPhone={syncSessionToPhone}
          setSyncSessionToPhone={setSyncSessionToPhone}
          returnSpeech={returnSpeech}
          setReturnSpeech={setReturnSpeech}
          useChunkedWsMic={useChunkedWsMic}
          setUseChunkedWsMic={setUseChunkedWsMic}
          conversationId={conversationId}
          lkRoom={lkRoom}
          lkIdentity={lkIdentity}
          sessionId={sessionId}
          setSessionId={setSessionId}
          onAudioPick={onAudioPick}
          result={result}
          onFetchSummary={fetchSummary}
          startMic={startMic}
          stopMicAndSend={stopMicAndSend}
          recording={recording}
          voiceSessionOn={voiceSessionOn}
        />
      </div>

      {livekitPublicUrl &&
      chatMode === "voice" &&
      voiceBackend === "livekit" &&
      roomReady &&
      liveKitBindings ? (
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
