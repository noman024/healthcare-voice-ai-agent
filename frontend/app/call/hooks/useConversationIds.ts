import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

import { newId } from "../callUtils";

/**
 * Stable per-tab transcript + summary key (conversation_id / persistence_session_id).
 * Never reset when switching text ↔ voice or WebSocket ↔ LiveKit so SQLite mirrors stay consistent.
 */
export function useConversationIds(): {
  conversationId: string;
  setConversationId: Dispatch<SetStateAction<string>>;
  sessionId: string;
  setSessionId: Dispatch<SetStateAction<string>>;
  roomReady: boolean;
} {
  const [conversationId, setConversationId] = useState("");
  const [sessionId, setSessionId] = useState("");

  useEffect(() => {
    const id = newId();
    setConversationId(id);
    setSessionId(id);
  }, []);

  const roomReady = conversationId.length > 0;

  return { conversationId, setConversationId, sessionId, setSessionId, roomReady };
}
