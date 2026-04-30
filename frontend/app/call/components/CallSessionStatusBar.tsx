export type CallSessionStatusBarProps = {
  transportLabel: string;
  conversationId: string;
  roomReady: boolean;
  sessionId: string;
  headerToolBanner: string | null;
};

export function CallSessionStatusBar({
  transportLabel,
  conversationId,
  roomReady,
  sessionId,
  headerToolBanner,
}: CallSessionStatusBarProps) {
  return (
    <div
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-zinc-800/60 bg-zinc-950/55 px-4 py-2 text-[11px] text-zinc-400 md:px-6"
      role="status"
      aria-live="polite"
    >
      <span className="rounded-md bg-zinc-800/90 px-2 py-0.5 font-medium text-zinc-200">
        {transportLabel}
      </span>
      <span className="hidden text-zinc-600 sm:inline" aria-hidden>
        ·
      </span>
      <span
        title={`Transcript + DB persistence key — unchanged when switching text / WebSocket / LiveKit: ${conversationId}`}
      >
        Chat key:{" "}
        <code className="rounded bg-zinc-900 px-1 font-mono text-zinc-200">
          {roomReady ? `${conversationId.slice(0, 12)}…` : "…"}
        </code>
      </span>
      <span className="hidden text-zinc-600 sm:inline" aria-hidden>
        ·
      </span>
      <span title="Tool/booking session id for the API (normalized phone after a successful identify_user when sync is on)">
        Session:{" "}
        <code className="rounded bg-zinc-900 px-1 font-mono text-zinc-200">{sessionId || "…"}</code>
      </span>
      {headerToolBanner ? (
        <>
          <span className="hidden text-zinc-600 sm:inline" aria-hidden>
            ·
          </span>
          <span
            className="max-w-[min(100%,28rem)] truncate font-medium text-teal-300/95"
            title={headerToolBanner}
          >
            Tool: {headerToolBanner}
          </span>
        </>
      ) : null}
    </div>
  );
}
