"use client";

import { createLocalTracks, Room, RoomEvent } from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";

type Props = {
  apiBase: string;
};

const envDefaultRoom = (process.env.NEXT_PUBLIC_LIVEKIT_DEFAULT_ROOM ?? "").trim();
export const DEFAULT_PUBLIC_LIVEKIT_ROOM_NAME = envDefaultRoom || "healthcare-demo";

/**
 * Optional WebRTC: browser joins LiveKit and publishes the microphone. The **livekit-agents**
 * worker (``scripts/run_voice_worker.py``) handles VAD, STT, LLM, TTS, and tool calls against
 * the same SQLite DB as FastAPI.
 *
 * REST + ``/ws/*`` on this page are unchanged when LiveKit is disabled.
 */
export default function LiveKitPanel({ apiBase }: Props) {
  const livekitUrl = (process.env.NEXT_PUBLIC_LIVEKIT_URL ?? "").trim().replace(/\/$/, "");
  const roomRef = useRef<Room | null>(null);
  const [connected, setConnected] = useState(false);
  const [roomName, setRoomName] = useState(DEFAULT_PUBLIC_LIVEKIT_ROOM_NAME);
  const [status, setStatus] = useState<string>(livekitUrl ? "idle" : "disabled (set NEXT_PUBLIC_LIVEKIT_URL)");
  const [busy, setBusy] = useState(false);

  const disconnect = useCallback(async () => {
    const r = roomRef.current;
    roomRef.current = null;
    setConnected(false);
    if (r && r.state !== "disconnected") await r.disconnect();
    setStatus(livekitUrl ? "idle" : "disabled (set NEXT_PUBLIC_LIVEKIT_URL)");
  }, [livekitUrl]);

  useEffect(() => {
    return () => {
      void disconnect();
    };
  }, [disconnect]);

  const connect = useCallback(async () => {
    if (!livekitUrl) return;
    const room = roomName.trim() || DEFAULT_PUBLIC_LIVEKIT_ROOM_NAME;
    setBusy(true);
    setStatus("fetching token…");
    try {
      const ident = `web-${Math.random().toString(36).slice(2, 10)}`;
      const tr = await fetch(
        `${apiBase}/livekit/token?room=${encodeURIComponent(room)}&identity=${encodeURIComponent(ident)}`,
      );
      if (!tr.ok) {
        const body = await tr.json().catch(() => ({}));
        setStatus(`token HTTP ${tr.status}: ${JSON.stringify(body)} (fallback: REST/WebSocket)`);
        setBusy(false);
        return;
      }
      const { token } = (await tr.json()) as { token?: string };
      if (!token) {
        setStatus("No token in response");
        setBusy(false);
        return;
      }
      const r = new Room();
      roomRef.current = r;
      r.on(RoomEvent.Disconnected, () => {
        setStatus("disconnected");
        setConnected(false);
        roomRef.current = null;
      });
      r.on(RoomEvent.Connected, () => setStatus(`connected (${room})`));
      setStatus("connecting…");
      await r.connect(livekitUrl, token);
      setConnected(true);
      try {
        const tracks = await createLocalTracks({ audio: true, video: false });
        const au = tracks[0];
        if (au) {
          await r.localParticipant.publishTrack(au);
          setStatus(`connected (${room}) · microphone on`);
        } else {
          setStatus(`connected (${room}) · no microphone track`);
        }
      } catch (e) {
        setStatus(
          `connected (${room}) · mic: ${e instanceof Error ? e.message : "failed — grant permission?"}`,
        );
      }
    } catch (e) {
      setConnected(false);
      const left = roomRef.current;
      roomRef.current = null;
      if (left && left.state !== "disconnected") {
        try {
          await left.disconnect();
        } catch {
          /* ignore */
        }
      }
      setStatus(e instanceof Error ? `${e.message} — use HTTP/WebSocket agent` : "connect failed");
    } finally {
      setBusy(false);
    }
  }, [apiBase, livekitUrl, roomName]);

  if (!livekitUrl) {
    return (
      <div className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400">
        LiveKit WebRTC: set <code className="font-mono">NEXT_PUBLIC_LIVEKIT_URL</code> (e.g. ws://127.0.0.1:7880)
        and API <code className="font-mono">LIVEKIT_*</code> keys. Voice still works via REST/WebSocket.
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-900">
      <p className="font-medium text-zinc-700 dark:text-zinc-200">LiveKit (optional WebRTC)</p>
      <p className="text-zinc-600 dark:text-zinc-400">
        Start <code className="rounded bg-zinc-100 px-1 font-mono dark:bg-zinc-800">run_voice_worker.py</code> from{" "}
        <code className="font-mono">backend/</code> after FastAPI and the LiveKit server. Room name should match what
        you type here so the agent joins the same room.
      </p>
      <label className="block text-zinc-600 dark:text-zinc-400">
        Room name
        <input
          type="text"
          value={roomName}
          onChange={(e) => setRoomName(e.target.value)}
          disabled={busy || connected}
          className="mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-[11px] dark:border-zinc-600 dark:bg-zinc-950"
        />
      </label>
      <p className="text-zinc-600 dark:text-zinc-400">Status: {status}</p>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || connected}
          onClick={() => void connect()}
          className="rounded-md bg-teal-700 px-2 py-1 text-white hover:bg-teal-600 disabled:opacity-50"
        >
          Connect
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void disconnect()}
          className="rounded-md border border-zinc-300 px-2 py-1 dark:border-zinc-600"
        >
          Disconnect
        </button>
      </div>
    </div>
  );
}
