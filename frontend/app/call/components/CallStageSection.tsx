import type { MutableRefObject } from "react";

import AvatarVoice from "../AvatarVoice";
import type { FeedItem } from "../callTypes";

export type CallStageSectionProps = {
  speaking: boolean;
  recording: boolean;
  listeningSurface: boolean;
  wsVoicePath: boolean;
  voiceSessionOn: boolean;
  liveKitSurfaceOn: boolean;
  liveKitMicLive: boolean;
  micVizStream: MediaStream | null;
  playbackAnalyserRef: MutableRefObject<AnalyserNode | null>;
  musetalkPortraitUrl: string | null;
  lipsyncVideoUrl: string | null;
  lipsyncSuppressVideoEnd: boolean;
  lipsyncSyncStartPerfMs: number | null;
  onLipsyncPlaybackEnd: () => void;
  statusLine: FeedItem[];
  busy: boolean;
  roomReady: boolean;
  lkRoom: string;
};

export function CallStageSection({
  speaking,
  recording,
  listeningSurface,
  wsVoicePath,
  voiceSessionOn,
  liveKitSurfaceOn,
  liveKitMicLive,
  micVizStream,
  playbackAnalyserRef,
  musetalkPortraitUrl,
  lipsyncVideoUrl,
  lipsyncSuppressVideoEnd,
  lipsyncSyncStartPerfMs,
  onLipsyncPlaybackEnd,
  statusLine,
  busy,
  roomReady,
  lkRoom,
}: CallStageSectionProps) {
  return (
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
        {statusLine.length > 0 || busy ? (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 px-3 py-2">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
              Agent activity
            </p>
            <ul className="space-y-1 text-xs text-zinc-300">
              {statusLine.length === 0 && busy ? (
                <li className="text-zinc-500">Processing…</li>
              ) : null}
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
              Live transcript lines are mirrored from{" "}
              <strong className="text-zinc-400">room transcription</strong> (user speech +
              assistant) when Voice uses LiveKit, and from REST/WebSocket when that path runs.
            </p>
            <p>
              If the status hint ends with{" "}
              <code className="rounded bg-zinc-950 px-1 font-mono">— switched to WebSocket</code>,
              something failed the LiveKit connect (timeouts, mic, token) and Voice fell back—the
              WebSocket path does not reuse the same LiveKit audio.
            </p>
            <p>
              If you see transcript but no spoken reply, the browser often blocks remote audio until
              microphone access and explicit playback start; tap the page if the hint mentions
              autoplay.
            </p>
          </div>
        </details>
      </div>
    </section>
  );
}
