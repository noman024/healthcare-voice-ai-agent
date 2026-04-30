import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useConversationIds } from "./useConversationIds";

describe("useConversationIds", () => {
  it("sets conversation and session ids after mount", async () => {
    const { result } = renderHook(() => useConversationIds());

    await waitFor(() => {
      expect(result.current.roomReady).toBe(true);
    });

    expect(result.current.conversationId.length).toBeGreaterThan(0);
    expect(result.current.sessionId).toBe(result.current.conversationId);
  });
});
