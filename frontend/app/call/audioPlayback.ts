import type { PlaybackAnalyserRef, TtsLifecycleRef } from "./callTypes";

/** Hands-free: endpoint speech after ~800ms silence; barge-in after a few loud ticks during TTS. */
export const VAD_INTERVAL_MS = 50;
export const VAD_RMS_THRESHOLD = 0.036;
export const VAD_LOUD_TICKS_TO_START = 2;
export const VAD_QUIET_TICKS_TO_END = 16;
export const VAD_MIN_UTTERANCE_MS = 450;
export const VAD_BARGE_IN_LOUD_TICKS = 3;
/** After TTS starts, ignore barge-in this long (ms) so speaker→mic bleed does not cancel playback. */
export const TTS_BARGE_IN_GRACE_MS = 1_400;
/** Stricter RMS multiplier for barge vs VAD (echo is often below true user interrupt). */
export const VAD_BARGE_RMS_MULT = 3.25;

export function pcmRmsTimeDomain(analyser: AnalyserNode): number {
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
export async function playWavBase64(
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
