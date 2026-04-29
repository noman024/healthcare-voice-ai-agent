"use client";

import {
  ConnectionState,
  createLocalTracks,
  Room,
  RoomEvent,
} from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";

const AGENT_DATA_TOPIC =
  typeof process !== "undefined" && typeof process.env.NEXT_PUBLIC_LIVEKIT_AGENT_TOPIC === "string"
    ? process.env.NEXT_PUBLIC_LIVEKIT_AGENT_TOPIC || "lk-agent-v1"
    : "lk-agent-v1";

type Props = {
  apiBase: string;
  /** Browser tab must use the SAME room token as LIVEKIT_ROOM running ``livekit_agent_worker.py`` */
  room: Room | null;
};

/**
 * Publish mic audio to LiveKit so the Python agent worker can subscribe, then mirror
 * ``/ws/conversation_audio`` semantics via reliable ``publishData`` (start/finalize JSON).
 *
 * Fallback: ``/conversation`` REST + MediaRecorder stays available on this page alongside this panel.
 */
export default function LiveKitVoiceBridge({ apiBase: _apiBase, room }: Props) {
  void _apiBase;
  const [sessionId, setSessionId] = useState("lk-live");
  const [returnSpeech] = useState(false);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const unsubRef = useRef<(() => void) | null>(null);

  const appendLog = useCallback((s: string) => {
    const line =
      `[${new Date().toISOString().slice(11, 19)}] ` +
      (s.length > 300 ? `${s.slice(0, 300)}…` : s);
    setLogLines((prev) => [line, ...prev].slice(0, 42));
  }, []);

  useEffect(() => {
    if (!room) return () => {};

    const onData = (
      payload: Uint8Array,
      _participant: unknown,
      _kind?: unknown,
      topic?: string,
    ): void => {
      if (topic && topic !== AGENT_DATA_TOPIC) return;
      try {
        const text = new TextDecoder().decode(payload);
        appendLog(text);
      } catch {
        appendLog(`(${payload.byteLength} bytes)`);
      }
    };

    room.on(RoomEvent.DataReceived, onData);

    unsubRef.current = () => room.off(RoomEvent.DataReceived, onData);

    return () => {
      unsubRef.current?.();
      unsubRef.current = null;
    };
  }, [appendLog, room]);

  const publishJson = async (body: Record<string, unknown>) => {
    if (!room || room.state !== ConnectionState.Connected) {
      appendLog("not connected — connect LiveKit first");
      return;
    }
    const enc = new TextEncoder();
    await room.localParticipant.publishData(enc.encode(JSON.stringify(body)), {
      reliable: true,
      topic: AGENT_DATA_TOPIC,
    });
  };

  const startMicPublish = async () => {
    if (!room || room.state !== ConnectionState.Connected) return;
    setBusy(true);
    try {
      const tracks = await createLocalTracks({ audio: true, video: false });
      const au = tracks[0];
      if (!au) {
        appendLog("no microphone track");
        return;
      }
      await room.localParticipant.publishTrack(au);
      setRecording(true);
      appendLog("Microphone published to room (PCM → worker subscribes).");
    } catch (e) {
      appendLog(e instanceof Error ? e.message : "mic publish failed");
    } finally {
      setBusy(false);
    }
  };

  const sendStart = async () => {
    setBusy(true);
    try {
      await publishJson({
        action: "start",
        session_id: sessionId.trim() || "default",
        language: null,
        return_speech: returnSpeech,
        file_extension: ".wav",
      });
      appendLog('Sent action=start (PCM → finalize uses ".wav").');
    } finally {
      setBusy(false);
    }
  };

  const sendFinalize = async () => {
    setBusy(true);
    try {
      await publishJson({ action: "finalize" });
      appendLog("Sent action=finalize — worker runs STT → agent pipeline.");
    } finally {
      setBusy(false);
    }
  };

  const sendPing = async () => {
    await publishJson({ action: "ping" });
  };

  if (!room) return null;

  return (
    <div className="mt-4 space-y-2 border-t border-zinc-200 pt-4 text-xs dark:border-zinc-600">
      <p className="font-medium text-zinc-700 dark:text-zinc-200">Agent bridge (data + mic)</p>
      <p className="text-zinc-500 dark:text-zinc-400">
        Run <code className="rounded bg-zinc-100 px-1 dark:bg-zinc-800">python scripts/livekit_agent_worker.py</code>
        — same{" "}
        <code className="rounded bg-zinc-100 px-1 font-mono dark:bg-zinc-800">LIVEKIT_ROOM</code> as above. Topic{" "}
        <code className="font-mono">{AGENT_DATA_TOPIC}</code>.
      </p>
      <label className="block">
        Session ID
        <input
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
          className="mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono dark:border-zinc-600 dark:bg-zinc-950"
        />
      </label>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void startMicPublish()}
          className="rounded-md bg-violet-700 px-2 py-1 text-white hover:bg-violet-600 disabled:opacity-50"
        >
          Publish mic to room
        </button>
        <button type="button" disabled={busy} onClick={() => void sendStart()} className="rounded-md border px-2 py-1">
          Send start (buffer)
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void sendFinalize()}
          className="rounded-md bg-emerald-800 px-2 py-1 text-white hover:bg-emerald-700 disabled:opacity-40"
        >
          Send finalize (agent)
        </button>
        <button type="button" disabled={busy} onClick={() => void sendPing()} className="rounded-md border px-2 py-1">
          Ping
        </button>
      </div>
      <details className="rounded border border-zinc-200 bg-zinc-50 p-2 dark:border-zinc-700 dark:bg-zinc-950/40">
        <summary className="cursor-pointer font-medium text-zinc-600 dark:text-zinc-300">Event log</summary>
        <ul className="mt-2 max-h-40 space-y-1 overflow-auto font-mono text-[11px] text-zinc-700 dark:text-zinc-400">
          {logLines.map((ln, i) => (
            <li key={`${i}-${ln.slice(0, 40)}`} className="whitespace-pre-wrap break-all">
              {ln}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
