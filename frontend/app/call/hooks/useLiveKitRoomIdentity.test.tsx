import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useLiveKitRoomIdentity } from "./useLiveKitRoomIdentity";

describe("useLiveKitRoomIdentity", () => {
  it("derives room and identity from conversation and session", () => {
    const { result } = renderHook(() => useLiveKitRoomIdentity("conv-uuid-here", "+15551234567"));
    expect(result.current.lkRoom.length).toBeGreaterThan(0);
    expect(result.current.lkIdentity).toContain("15551234567");
  });
});
