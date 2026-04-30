"use client";

import { ParticipantKind, Room, RoomEvent, Track, TrackEvent } from "livekit-client";
import type {
  Participant,
  RemoteParticipant,
  RemoteTrack,
  RemoteTrackPublication,
  TranscriptionSegment,
} from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";

type Props = {
  apiBase: string;
  /** When true, acquire token (if configured) and connect; when false, disconnect. */
  active: boolean;
  roomName: string;
  identity: string;
  /** Called after token/connect/mic failures so parent can fall back (e.g. WebSocket voice). */
  onConnectionFailed?: (message: string) => void;
  /** Connected to room (mic may still be settling). */
  onConnected?: () => void;
  onDisconnected?: () => void;
  /** Surfaced status for optional UI/debug. */
  onStatusChange?: (status: string) => void;
  /** Called after local mic track is published (assistant may still be joining). */
  onMicPublished?: () => void;
  /**
   * Room transcription segments (published by LiveKit Agents for user STT and assistant speech).
   * Same shape as ``appendExchange`` on the call page transcript.
   */
  onTranscriptExchange?: (user: string | null, assistant: string | null) => void;
};

export function sanitizeLiveKitRoomName(room: string, fallback: string): string {
  const t = room.trim().replace(/\s+/g, "-").slice(0, 128);
  return t || fallback;
}

export function sanitizeLiveKitIdentity(ident: string, fallback: string): string {
  const t = ident.trim().replace(/[^\w._-]/g, "-").slice(0, 128);
  return t || fallback;
}

const envDefaultRoom = (process.env.NEXT_PUBLIC_LIVEKIT_DEFAULT_ROOM ?? "").trim();
/** Public default room label from env or demo name (used only as fallback input). */
export const DEFAULT_PUBLIC_LIVEKIT_ROOM_NAME = envDefaultRoom || "healthcare-demo";

/** When ``NEXT_PUBLIC_LIVEKIT_DIAG=1``, logs factual client state (no guessing) to DevTools ``console``. */
function liveKitDiagEnabled(): boolean {
  return (process.env.NEXT_PUBLIC_LIVEKIT_DIAG ?? "").trim() === "1";
}

function diagLog(label: string, data?: Record<string, unknown>): void {
  if (!liveKitDiagEnabled()) return;
  if (data !== undefined) {
    console.debug(`[LiveKit DIAG] ${label}`, data);
  } else {
    console.debug(`[LiveKit DIAG] ${label}`);
  }
}

/**
 * Controlled LiveKit: connects when ``active``, disconnects otherwise.
 * Runs the **livekit-agents** pipeline when ``scripts/run_voice_worker.py`` is up.
 */
export default function LiveKitPanel({
  apiBase,
  active,
  roomName,
  identity,
  onConnectionFailed,
  onConnected,
  onDisconnected,
  onStatusChange,
  onMicPublished,
  onTranscriptExchange,
}: Props) {
  const livekitUrl = (process.env.NEXT_PUBLIC_LIVEKIT_URL ?? "").trim().replace(/\/$/, "");
  const roomRef = useRef<Room | null>(null);
  const mountedRef = useRef(true);
  /** Keep notifier out of connectFlow deps; unstable identity would remount/disconnect each render after mic publishes. */
  const onMicPublishedRef = useRef(onMicPublished);
  useEffect(() => {
    onMicPublishedRef.current = onMicPublished;
  }, [onMicPublished]);

  const onTranscriptExchangeRef = useRef(onTranscriptExchange);
  useEffect(() => {
    onTranscriptExchangeRef.current = onTranscriptExchange;
  }, [onTranscriptExchange]);

  const transcriptionDedupeRef = useRef(new Set<string>());
  const transcriptionListenerRef = useRef<
    ((segments: TranscriptionSegment[], participant?: Participant) => void) | null
  >(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const [busy, setBusy] = useState(false);

  const report = useCallback(
    (s: string) => {
      onStatusChange?.(s);
    },
    [onStatusChange],
  );

  const disconnectSilent = useCallback(async () => {
    agentWaitCleanupRef.current?.();
    agentWaitCleanupRef.current = null;
    const r = roomRef.current;
    const tr = transcriptionListenerRef.current;
    if (r && tr) r.off(RoomEvent.TranscriptionReceived, tr);
    transcriptionListenerRef.current = null;
    transcriptionDedupeRef.current.clear();
    roomRef.current = null;
    if (r && r.state !== "disconnected") await r.disconnect();
  }, []);

  const activeRef = useRef(false);
  /** Clears agent-join wait timer + room listeners (connectFlow / unmount). */
  const agentWaitCleanupRef = useRef<(() => void) | null>(null);
  /** Incremented on StrictMode remount / deactivate to cancel in-flight connects. */
  const connectGenerationRef = useRef(0);

  const connectFlow = useCallback(
    async (wave: number) => {
      const stillCurrent = () => wave === connectGenerationRef.current;

      if (!livekitUrl) {
        report("missing NEXT_PUBLIC_LIVEKIT_URL");
        onConnectionFailed?.("LiveKit URL not configured");
        return;
      }

      await disconnectSilent();
      if (!stillCurrent()) return;

      const room = roomName.trim();
      const ident = identity.trim();

      setBusy(true);
      report("fetching LiveKit token…");
      try {
        const tr = await fetch(
          `${apiBase}/livekit/token?room=${encodeURIComponent(room)}&identity=${encodeURIComponent(ident)}`,
        );
        if (!stillCurrent()) return;

        if (!tr.ok) {
          const body = await tr.json().catch(() => ({}));
          const msg = `LiveKit token HTTP ${tr.status}: ${JSON.stringify(body)}`;
          report(msg);
          onConnectionFailed?.(msg);
          return;
        }
        const body = (await tr.json()) as { token?: string };
        if (!body.token) {
          report("LiveKit token response missing JWT");
          onConnectionFailed?.("No token from API");
          return;
        }

        if (!stillCurrent()) return;

        const assistantWebAudioMix = (process.env.NEXT_PUBLIC_LIVEKIT_WEB_AUDIO_MIX ?? "").trim() === "1";
        /** SDK default is false; Web Audio routing mutes `<audio>` and pipes via AudioContext—in practice that often yields silence if mixing fails. Opt in with NEXT_PUBLIC_LIVEKIT_WEB_AUDIO_MIX=1. */
        const r = new Room({
          webAudioMix: assistantWebAudioMix,
        });
        roomRef.current = r;
        agentWaitCleanupRef.current?.();
        agentWaitCleanupRef.current = null;

        const detachTranscriptionFromThisRoom = (): void => {
          const ln = transcriptionListenerRef.current;
          if (ln) {
            try {
              r.off(RoomEvent.TranscriptionReceived, ln);
            } catch {
              /* ignore */
            }
            transcriptionListenerRef.current = null;
          }
          transcriptionDedupeRef.current.clear();
        };

        const fail = async (reason: string) => {
          if (!mountedRef.current) return;
          report(reason);
          onConnectionFailed?.(reason);
          detachTranscriptionFromThisRoom();
          roomRef.current = null;
          if (r.state !== "disconnected") {
            try {
              await r.disconnect();
            } catch {
              /* ignore */
            }
          }
        };

        r.on(RoomEvent.Disconnected, () => {
          if (!mountedRef.current) return;
          report("LiveKit disconnected");
          if (roomRef.current === r) {
            detachTranscriptionFromThisRoom();
            roomRef.current = null;
          }
        });

        r.on(RoomEvent.Connected, () => {
          report(`LiveKit / ${room}`);
        });

        if (liveKitDiagEnabled()) {
          r.on(RoomEvent.AudioPlaybackStatusChanged, (playing: boolean) => {
            diagLog("Room.AudioPlaybackStatusChanged", { playing, roomState: r.state });
          });
        }

        report("Connecting to LiveKit…");
        await r.connect(livekitUrl, body.token);

        if (!mountedRef.current || !stillCurrent()) {
          detachTranscriptionFromThisRoom();
          roomRef.current = null;
          await r.disconnect().catch(() => undefined);
          return;
        }

        transcriptionDedupeRef.current.clear();
        const browserIdentity = r.localParticipant.identity;
        const onRoomTranscription = (
          segments: TranscriptionSegment[],
          participant?: Participant,
        ): void => {
          if (!participant || !mountedRef.current) return;
          const relay = onTranscriptExchangeRef.current;
          if (!relay) return;
          for (const seg of segments) {
            if (!seg.final) continue;
            const line = seg.text.trim();
            if (!line) continue;
            const key = `${participant.identity}:${seg.id}`;
            if (transcriptionDedupeRef.current.has(key)) continue;
            transcriptionDedupeRef.current.add(key);
            if (participant.identity === browserIdentity) relay(line, null);
            else relay(null, line);
          }
        };
        transcriptionListenerRef.current = onRoomTranscription;
        r.on(RoomEvent.TranscriptionReceived, onRoomTranscription);

        /** Unlock remote playback; standalone createLocalTracks does not emit AudioStreamAcquired on the participant. */
        const playbackDiagSid = new Set<string>();
        const kickAssistantPlayback = (): void => {
          void r
            .startAudio()
            .then(() => {
              diagLog("Room.startAudio resolved", {
                canPlaybackAudio: r.canPlaybackAudio,
                roomState: r.state,
              });
            })
            .catch((err: unknown) => {
              if (!mountedRef.current) return;
              diagLog("Room.startAudio rejected", {
                name: err instanceof Error ? err.name : "unknown",
                message: err instanceof Error ? err.message : String(err),
              });
              const msg =
                err instanceof Error && err.name === "NotAllowedError"
                  ? "Assistant audio paused: click or tap anywhere on this page once (browser autoplay policy)."
                  : `Assistant audio playback: ${err instanceof Error ? err.message : ""}`;
              report(msg.trim());
            });
        };

        /** Remote subscribe does not call ``attach()``; ``startAudio`` only plays attached elements. */
        const attachRemoteAssistantAudio = (track: RemoteTrack): void => {
          if (track.kind !== Track.Kind.Audio) return;
          const pk = `${track.sid}:${track.mediaStreamTrack?.id ?? "?"}`;
          if (!playbackDiagSid.has(pk)) {
            playbackDiagSid.add(pk);
            track.on(TrackEvent.AudioPlaybackStarted, () => {
              diagLog("track.AudioPlaybackStarted", { sid: track.sid, muted: track.isMuted });
            });
            track.on(TrackEvent.AudioPlaybackFailed, (e: unknown) => {
              diagLog("track.AudioPlaybackFailed", {
                sid: track.sid,
                name: e instanceof Error ? e.name : "unknown",
                message: e instanceof Error ? e.message : String(e),
              });
            });
          }
          const attachedBefore = track.attachedElements.length;
          diagLog("attachRemoteAssistantAudio(pre)", {
            pk,
            attachedBefore,
            trackMutedProp: track.isMuted,
            mediaTrackEnabled: track.mediaStreamTrack?.enabled,
            mediaTrackMuted: track.mediaStreamTrack?.muted,
            mediaReadyState: track.mediaStreamTrack?.readyState,
          });
          if (track.attachedElements.length > 0) {
            kickAssistantPlayback();
            return;
          }
          try {
            const el = track.attach();
            if ("setVolume" in track && typeof track.setVolume === "function") {
              (track as { setVolume: (v: number) => void }).setVolume(1);
            }
            el.autoplay = true;
            el.setAttribute("playsinline", "true");
            /* With webAudioMix, the SDK deliberately mutes the element and pumps via AudioContext — do not undo. */
            if (!assistantWebAudioMix) {
              el.muted = false;
            }
            if (!el.parentElement) {
              el.style.display = "none";
              document.body.append(el);
            }
            diagLog("attachRemoteAssistantAudio(post)", {
              pk,
              attachedAfter: track.attachedElements.length,
              elMuted: el.muted,
              elPaused: el.paused,
              inDom: !!el.parentElement,
            });
          } catch (err: unknown) {
            if (!mountedRef.current) return;
            diagLog("attachRemoteAssistantAudio threw", {
              message: err instanceof Error ? err.message : String(err),
            });
            report(`Assistant audio attach: ${err instanceof Error ? err.message : String(err)}`);
          }
          kickAssistantPlayback();
        };

        const syncAssistantAudioTracks = (): void => {
          r.remoteParticipants.forEach((part) => {
            part.audioTrackPublications.forEach((pub) => {
              const tr = pub.track;
              if (tr) attachRemoteAssistantAudio(tr);
            });
          });
        };

        report("Publishing microphone…");
        try {
          const tracks = await r.localParticipant.createTracks({ audio: true, video: false });
          const au = tracks.find((t) => t.kind === Track.Kind.Audio);
          if (au) {
            await r.localParticipant.publishTrack(au);
            report(`LiveKit / ${room} / mic on`);
            onMicPublishedRef.current?.();
            kickAssistantPlayback();
            syncAssistantAudioTracks();
          } else {
            await fail("LiveKit: no microphone track");
            return;
          }
        } catch (e) {
          await fail(`LiveKit mic failed: ${e instanceof Error ? e.message : "grant permission?"}`);
          return;
        }

        if (!mountedRef.current || !stillCurrent()) {
          detachTranscriptionFromThisRoom();
          roomRef.current = null;
          await r.disconnect().catch(() => undefined);
          return;
        }

        const agentWaitMs = Math.min(
          120_000,
          Math.max(
            25_000,
            Number(process.env.NEXT_PUBLIC_LIVEKIT_AGENT_WAIT_MS ?? "") || 60_000,
          ),
        );

        let idleTimer: number | undefined;
        let settled = false;

        function teardownAgentWait(): void {
          agentWaitCleanupRef.current = null;
          if (idleTimer !== undefined) {
            window.clearTimeout(idleTimer);
            idleTimer = undefined;
          }
          r.off(RoomEvent.ParticipantConnected, onParticipantJoined);
          r.off(RoomEvent.ParticipantDisconnected, onParticipantLeft);
          r.off(RoomEvent.ParticipantActive, onParticipantActive);
          /* Keep TrackSubscribed: agent often connects before audio is subscribed; removing this caused missed attach/play. */
        }

        function signalReady(reason: string): void {
          if (settled || !mountedRef.current) return;
          settled = true;
          teardownAgentWait();
          report(`${reason}`);
          onConnected?.();
        }

        async function signalTimeout(): Promise<void> {
          if (settled || !mountedRef.current) return;
          settled = true;
          teardownAgentWait();
          await fail(
            `No assistant detected after ${Math.round(agentWaitMs / 1000)}s — is run_voice_worker.py running (same LIVEKIT_URL and API keys)?`,
          );
        }

        function agentPresentHint(p: Participant): string | null {
          if (p === r.localParticipant) return null;
          const rp = p as RemoteParticipant;
          if (rp.kind === ParticipantKind.AGENT || rp.permissions?.agent) {
            return `LiveKit / ${room} / assistant connected`;
          }
          return `LiveKit / ${room} / remote (${rp.identity ?? "?"})`;
        }

        /** Prefer event payload — `remoteParticipants` can lag ParticipantConnected briefly. */
        function onParticipantJoined(p: Participant): void {
          const hint = agentPresentHint(p);
          if (hint) signalReady(hint);
        }

        function onParticipantActive(p: Participant): void {
          const hint = agentPresentHint(p);
          if (hint) signalReady(hint);
        }

        function onParticipantLeft(): void {
          if (settled || r.remoteParticipants.size > 0) return;
          report("Assistant left the room…");
        }

        function onRemoteTrack(
          track: RemoteTrack,
          pub: RemoteTrackPublication | undefined,
          p: RemoteParticipant,
        ): void {
          diagLog("Room.TrackSubscribed", {
            remoteIdentity: p.identity,
            kind: track.kind,
            trackSid: track.sid,
            publicationSid: pub?.trackSid,
            publicationMutedMetadata: pub?.isMuted ?? null,
          });
          attachRemoteAssistantAudio(track);
          const hint = agentPresentHint(p);
          if (hint) signalReady(`${hint} [audio subscribed]`);
        }

        r.on(RoomEvent.TrackSubscribed, onRemoteTrack);

        agentWaitCleanupRef.current = teardownAgentWait;

        for (const p of r.remoteParticipants.values()) {
          const hint = agentPresentHint(p);
          if (hint) {
            signalReady(`${hint} (already in room)`);
            break;
          }
        }

        if (settled) {
          syncAssistantAudioTracks();
          kickAssistantPlayback();
        }

        if (!settled) {
          report("Waiting for assistant (Python worker)…");
          r.on(RoomEvent.ParticipantConnected, onParticipantJoined);
          r.on(RoomEvent.ParticipantDisconnected, onParticipantLeft);
          r.on(RoomEvent.ParticipantActive, onParticipantActive);
          idleTimer = window.setTimeout(() => {
            void signalTimeout();
          }, agentWaitMs);
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : "LiveKit connect failed";
        report(`${msg}`);
        roomRef.current = null;
        onConnectionFailed?.(msg);
      } finally {
        if (mountedRef.current) setBusy(false);
      }
    },
    [
      apiBase,
      disconnectSilent,
      identity,
      livekitUrl,
      onConnected,
      onConnectionFailed,
      report,
      roomName,
    ],
  );

  useEffect(() => {
    if (!active) {
      if (activeRef.current) {
        activeRef.current = false;
        connectGenerationRef.current += 1;
        void disconnectSilent().then(() => {
          report(livekitUrl ? "LiveKit disconnected" : "idle");
          onDisconnected?.();
        });
      }
      return;
    }

    if (!livekitUrl) {
      onConnectionFailed?.("LiveKit URL not configured");
      return;
    }

    activeRef.current = true;
    connectGenerationRef.current += 1;
    const wave = connectGenerationRef.current;
    void connectFlow(wave);

    return () => {
      connectGenerationRef.current += 1;
      void disconnectSilent();
    };
  }, [
    active,
    livekitUrl,
    roomName,
    identity,
    apiBase,
    connectFlow,
    disconnectSilent,
    onConnectionFailed,
    onDisconnected,
    report,
  ]);

  return (
    <div className="sr-only" aria-live="polite">
      {/* Controlled LiveKit worker; UI status via ``onStatusChange`` */}
      {livekitUrl ? `LiveKit ${busy ? "busy" : "idle"}` : "LiveKit inactive"}
    </div>
  );
}
