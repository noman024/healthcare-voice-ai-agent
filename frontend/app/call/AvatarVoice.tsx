"use client";

import { useEffect, useRef } from "react";

type Props = {
  /** True while TTS or assistant audio is playing */
  speaking: boolean;
  /** True while microphone capture is active */
  recording: boolean;
  /** Live mic stream while recording (drives input level meter) */
  mediaStream: MediaStream | null;
  className?: string;
};

/**
 * Circular avatar with a small canvas level meter: live mic bins while recording,
 * soft animated bars while speaking (smooth under load without extra WebAudio wiring).
 */
export default function AvatarVoice({ speaking, recording, mediaStream, className = "" }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
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
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const w = canvas.width;
    const h = canvas.height;
    const nBars = 9;
    const data = new Uint8Array(analyserRef.current ? analyserRef.current.frequencyBinCount : nBars);

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
      } else if (speaking) {
        const t = Date.now() / 180;
        for (let i = 0; i < nBars; i++) {
          const wobble = 0.35 + 0.65 * Math.abs(Math.sin(t + i * 0.55));
          levels.push(wobble * (0.55 + 0.45 * Math.random()));
        }
      } else {
        levels = Array(nBars).fill(0.08);
      }

      for (let i = 0; i < nBars; i++) {
        const amp = levels[i] ?? 0.05;
        const bh = Math.max(4, amp * (h - 8));
        const x = gap + i * (barW + gap);
        const y = h - bh - gap;
        ctx.fillStyle =
          speaking || recording
            ? "rgba(16, 185, 129, 0.85)"
            : "rgba(113, 113, 122, 0.45)";
        ctx.beginPath();
        ctx.fillRect(x, y, barW, bh);
      }
      rafRef.current = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [speaking, recording, mediaStream]);

  return (
    <div className={`flex flex-col items-center ${className}`}>
      <div
        className={`relative flex h-48 w-48 items-center justify-center rounded-full border-4 border-emerald-500/40 bg-gradient-to-br from-emerald-400/30 to-teal-600/40 shadow-xl transition-transform duration-300 dark:border-emerald-400/30 ${
          speaking ? "scale-105" : "scale-100"
        }`}
        aria-hidden
      >
        <span className="pointer-events-none select-none text-5xl opacity-90">🗣️</span>
        <canvas
          ref={canvasRef}
          width={160}
          height={44}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-md bg-black/15 dark:bg-black/30"
        />
      </div>
      <p className="mt-4 text-center text-xs text-zinc-500 dark:text-zinc-400">
        Waveform: live mic while recording; motion sync while assistant audio plays.
      </p>
    </div>
  );
}
