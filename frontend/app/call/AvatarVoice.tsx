"use client";

import { type MutableRefObject, useEffect, useRef, useState } from "react";

type Props = {
  /** True while TTS or assistant audio is playing */
  speaking: boolean;
  /** True while microphone capture is active */
  recording: boolean;
  /** Live mic stream while recording (drives input level meter) */
  mediaStream: MediaStream | null;
  /** When set, TTS playback levels drive the meter (lip-sync–style motion from real audio). */
  playbackAnalyserRef?: MutableRefObject<AnalyserNode | null>;
  /** Idle portrait — same image as MuseTalk ``MUSETALK_REFERENCE_IMAGE`` (GET /avatar/reference). */
  musetalkPortraitUrl?: string | null;
  /** Object URL for MuseTalk MP4 (embedded audio); takes over the avatar while set. */
  lipsyncVideoUrl?: string | null;
  /** When set, seek lipsync video to align with TTS (performance.now() − this value at `loadedmetadata`). */
  lipsyncSyncStartPerfMs?: number | null;
  /** When true, do not treat video end/error as session end (Piper WAV is playing separately). */
  lipsyncSuppressVideoEnd?: boolean;
  /** Called when lipsync video ends, errors, or is interrupted at the DOM level. */
  onLipsyncPlaybackEnd?: () => void;
  className?: string;
};

/**
 * Circular avatar: MuseTalk **portrait** at rest + **video** lipsync when TTS is playing;
 * otherwise optional legacy canvas meter (emoji + bars).
 */
export default function AvatarVoice({
  speaking,
  recording,
  mediaStream,
  playbackAnalyserRef,
  musetalkPortraitUrl = null,
  lipsyncVideoUrl = null,
  lipsyncSyncStartPerfMs = null,
  lipsyncSuppressVideoEnd = false,
  onLipsyncPlaybackEnd,
  className = "",
}: Props) {
  const [portraitBroken, setPortraitBroken] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const srcRef = useRef<MediaStreamAudioSourceNode | null>(null);

  useEffect(() => {
    if (!recording || !mediaStream) {
      if (srcRef.current) {
        try {
          srcRef.current.disconnect();
        } catch {
          /* ignore */
        }
        srcRef.current = null;
      }
      if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
        void audioCtxRef.current.close();
      }
      audioCtxRef.current = null;
      analyserRef.current = null;
      return;
    }
    const ac = new AudioContext();
    audioCtxRef.current = ac;
    const an = ac.createAnalyser();
    an.fftSize = 128;
    an.smoothingTimeConstant = 0.65;
    analyserRef.current = an;
    const src = ac.createMediaStreamSource(mediaStream);
    srcRef.current = src;
    src.connect(an);
    void ac.resume();
    return () => {
      try {
        src.disconnect();
      } catch {
        /* ignore */
      }
      void ac.close();
      srcRef.current = null;
      analyserRef.current = null;
      audioCtxRef.current = null;
    };
  }, [recording, mediaStream]);

  useEffect(() => {
    const url = lipsyncVideoUrl?.trim() || "";
    const v = videoRef.current;
    if (!url || !v) return;

    const syncStart = lipsyncSyncStartPerfMs;

    const end = (): void => {
      if (!lipsyncSuppressVideoEnd) onLipsyncPlaybackEnd?.();
    };

    /** Room / Piper audio starts at syncStart; MuseTalk MP4 arrives late — seek into the clip to match. */
    let playbackStarted = false;
    const seekToSyncedTime = (): boolean => {
      if (syncStart == null) return true;
      if (!Number.isFinite(v.duration) || v.duration <= 0 || Number.isNaN(v.duration)) return false;
      const elapsed = (performance.now() - syncStart) / 1000;
      const dur = v.duration;
      // MuseTalk often finishes seconds after room audio: seeking to `elapsed` jumps near the end and
      // fires `ended` immediately — looks like a static portrait + pulse. Play from start when we're late.
      if (elapsed >= dur - 0.08) {
        try {
          v.currentTime = 0;
        } catch {
          /* ignore */
        }
        return true;
      }
      const t = Math.min(Math.max(0, elapsed), Math.max(0, dur - 0.04));
      try {
        v.currentTime = t;
      } catch {
        /* ignore */
      }
      return true;
    };

    const startPlaybackOnce = (): void => {
      if (playbackStarted) return;
      if (syncStart != null && !seekToSyncedTime()) return;
      playbackStarted = true;
      void v.play().catch((err) => {
        if (process.env.NODE_ENV === "development") {
          console.warn("[AvatarVoice] lipsync video play() failed", err);
        }
        end();
      });
    };

    v.src = url;
    v.playsInline = true;
    v.muted = true;
    const onEnded = (): void => end();
    const onError = (): void => end();
    v.addEventListener("loadedmetadata", startPlaybackOnce);
    v.addEventListener("canplay", startPlaybackOnce);
    v.addEventListener("ended", onEnded);
    v.addEventListener("error", onError);
    v.load();
    return () => {
      v.removeEventListener("loadedmetadata", startPlaybackOnce);
      v.removeEventListener("canplay", startPlaybackOnce);
      v.removeEventListener("ended", onEnded);
      v.removeEventListener("error", onError);
      try {
        v.pause();
        v.removeAttribute("src");
        v.load();
      } catch {
        /* ignore */
      }
    };
  }, [lipsyncVideoUrl, lipsyncSyncStartPerfMs, lipsyncSuppressVideoEnd, onLipsyncPlaybackEnd]);

  const portraitSrc = musetalkPortraitUrl?.trim() || "";
  useEffect(() => {
    setPortraitBroken(false);
  }, [portraitSrc]);

  useEffect(() => {
    if (lipsyncVideoUrl || portraitSrc) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const w = canvas.width;
    const h = canvas.height;
    const mouthBand = 14;
    const barTop = mouthBand + 4;
    const barH = h - barTop - 4;
    const nBars = 9;
    const data = new Uint8Array(
      analyserRef.current ? analyserRef.current.frequencyBinCount : nBars,
    );

    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      const gap = 3;
      const barW = (w - gap * (nBars + 1)) / nBars;
      let levels: number[] = [];

      if (recording && analyserRef.current) {
        analyserRef.current.getByteFrequencyData(data);
        const step = Math.max(1, Math.floor(data.length / nBars));
        for (let i = 0; i < nBars; i++) {
          let s = 0;
          for (let j = 0; j < step; j++) s += data[i * step + j] ?? 0;
          levels.push(s / step / 255);
        }
      } else if (speaking && playbackAnalyserRef?.current) {
        const pb = playbackAnalyserRef.current;
        const bins = pb.frequencyBinCount;
        const playData = new Uint8Array(bins);
        pb.getByteFrequencyData(playData);
        const step = Math.max(1, Math.floor(playData.length / nBars));
        for (let i = 0; i < nBars; i++) {
          let s = 0;
          for (let j = 0; j < step; j++) s += playData[i * step + j] ?? 0;
          levels.push(s / step / 255);
        }
      } else if (speaking) {
        const t = Date.now() / 180;
        for (let i = 0; i < nBars; i++) {
          const wobble = 0.35 + 0.65 * Math.abs(Math.sin(t + i * 0.55));
          levels.push(wobble * (0.55 + 0.45 * Math.random()));
        }
      } else {
        levels = Array(nBars).fill(0.08);
      }

      const avg = levels.length > 0 ? levels.reduce((a, b) => a + b, 0) / levels.length : 0.08;
      const open = Math.min(1, avg * 1.35);
      const cx = w / 2;
      const cy = 7;
      const rx = 16 + open * 4;
      const ry = 2.5 + open * 7;
      ctx.fillStyle =
        speaking || recording ? "rgba(16, 185, 129, 0.35)" : "rgba(113, 113, 122, 0.22)";
      ctx.beginPath();
      ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle =
        speaking || recording ? "rgba(16, 185, 129, 0.95)" : "rgba(113, 113, 122, 0.55)";
      ctx.lineWidth = 1.5;
      ctx.stroke();

      for (let i = 0; i < nBars; i++) {
        const amp = levels[i] ?? 0.05;
        const bh = Math.max(3, amp * barH);
        const x = gap + i * (barW + gap);
        const y = barTop + barH - bh;
        ctx.fillStyle =
          speaking || recording ? "rgba(16, 185, 129, 0.85)" : "rgba(113, 113, 122, 0.45)";
        ctx.beginPath();
        ctx.fillRect(x, y, barW, bh);
      }
      rafRef.current = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [speaking, recording, mediaStream, playbackAnalyserRef, lipsyncVideoUrl, portraitSrc]);

  const showVideo = Boolean(lipsyncVideoUrl?.trim());
  const useMusetalkShell = Boolean(portraitSrc) && !portraitBroken;
  const showLegacyMeter = !showVideo && !useMusetalkShell;
  const musetalkIdleSpeaking = useMusetalkShell && speaking && !showVideo;

  return (
    <div className={`flex flex-col items-center ${className}`}>
      <div
        className={`relative flex h-48 w-48 items-center justify-center overflow-hidden rounded-full border-4 border-emerald-500/40 bg-gradient-to-br from-emerald-400/30 to-teal-600/40 shadow-xl transition-transform duration-300 dark:border-emerald-400/30 ${
          speaking || showVideo ? "scale-105" : "scale-100"
        } ${musetalkIdleSpeaking ? "ring-4 ring-emerald-400/35 ring-offset-2 ring-offset-zinc-950 animate-pulse" : ""}`}
        aria-hidden
      >
        {showVideo ? (
          <video
            ref={videoRef}
            className="absolute inset-0 h-full w-full object-cover"
            playsInline
            muted
            controls={false}
            disablePictureInPicture
          />
        ) : useMusetalkShell ? (
          // eslint-disable-next-line @next/next/no-img-element -- runtime URL from our API; small portrait
          <img
            src={portraitSrc}
            alt=""
            className="absolute inset-0 h-full w-full object-cover"
            onError={() => setPortraitBroken(true)}
          />
        ) : (
          <>
            <span className="pointer-events-none select-none text-5xl opacity-90" aria-hidden>
              🗣️
            </span>
            <canvas
              ref={canvasRef}
              width={160}
              height={54}
              className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-md bg-black/15 dark:bg-black/30"
            />
          </>
        )}
      </div>
      <p className="mt-4 max-w-[260px] text-center text-xs text-zinc-500 dark:text-zinc-400">
        {showVideo
          ? "MuseTalk lipsync (video); audio from Piper when using chat/WS, or from the room with LiveKit."
          : useMusetalkShell
            ? "MuseTalk avatar — speaking uses GPU lipsync video."
            : "Local avatar: Web Audio mouth meter. Set NEXT_PUBLIC_MUSETALK_ENABLED=1 for MuseTalk portrait + lipsync."}
      </p>
    </div>
  );
}
