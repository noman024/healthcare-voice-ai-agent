import { useMemo } from "react";

import {
  DEFAULT_PUBLIC_LIVEKIT_ROOM_NAME,
  sanitizeLiveKitIdentity,
  sanitizeLiveKitRoomName,
} from "../LiveKitPanel";

export function useLiveKitRoomIdentity(
  conversationId: string,
  sessionId: string,
): { lkRoom: string; lkIdentity: string } {
  return useMemo(
    () => ({
      lkRoom: sanitizeLiveKitRoomName(conversationId, DEFAULT_PUBLIC_LIVEKIT_ROOM_NAME),
      lkIdentity: sanitizeLiveKitIdentity(
        sessionId.trim(),
        `web-${(conversationId || "session").slice(0, 12)}`,
      ),
    }),
    [sessionId, conversationId],
  );
}
