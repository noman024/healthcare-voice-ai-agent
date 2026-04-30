import { describe, expect, it } from "vitest";

import {
  coerceNonNegInt,
  coerceOffsetMs,
  coerceVaLast,
  coerceVaSeq,
  httpToWsBase,
  mergeTtsWavChunks,
  newId,
  resolveClientApiBase,
} from "./callUtils";

describe("mergeTtsWavChunks", () => {
  it("reassembles ordered base64 chunks into WAV bytes", () => {
    const a = new Uint8Array([0x52, 0x49, 0x46, 0x46]); // "RIFF"
    const b = new Uint8Array([0x57, 0x41, 0x56, 0x45]); // "WAVE"
    const parts = new Map<number, string>([
      [0, btoa(String.fromCharCode(...a))],
      [1, btoa(String.fromCharCode(...b))],
    ]);
    const out = mergeTtsWavChunks(parts);
    expect(Array.from(out)).toEqual([...a, ...b]);
  });

  it("sorts out-of-order keys", () => {
    const ch0 = new Uint8Array([1, 2]);
    const ch1 = new Uint8Array([3, 4]);
    const parts = new Map<number, string>([
      [1, btoa(String.fromCharCode(...ch1))],
      [0, btoa(String.fromCharCode(...ch0))],
    ]);
    expect(Array.from(mergeTtsWavChunks(parts))).toEqual([1, 2, 3, 4]);
  });
});

describe("coerce helpers", () => {
  it("coerceVaSeq", () => {
    expect(coerceVaSeq(3)).toBe(3);
    expect(coerceVaSeq("4")).toBe(4);
    expect(coerceVaSeq("x")).toBe(-1);
  });

  it("coerceVaLast", () => {
    expect(coerceVaLast(true)).toBe(true);
    expect(coerceVaLast("1")).toBe(true);
    expect(coerceVaLast(false)).toBe(false);
  });

  it("coerceOffsetMs", () => {
    expect(coerceOffsetMs(10)).toBe(10);
    expect(coerceOffsetMs("5")).toBe(5);
    expect(coerceOffsetMs("bad")).toBe(0);
  });

  it("coerceNonNegInt", () => {
    expect(coerceNonNegInt(3.7)).toBe(3);
    expect(coerceNonNegInt("2")).toBe(2);
    expect(coerceNonNegInt(-1)).toBeNull();
  });
});

describe("resolveClientApiBase / httpToWsBase", () => {
  it("httpToWsBase swaps scheme", () => {
    expect(httpToWsBase("http://localhost:8000")).toBe("ws://localhost:8000");
    expect(httpToWsBase("https://example.com/api/")).toBe("wss://example.com/api");
  });

  it("resolveClientApiBase uses env when set", () => {
    const prev = process.env.NEXT_PUBLIC_API_URL;
    process.env.NEXT_PUBLIC_API_URL = "http://env.test:9000/";
    expect(resolveClientApiBase()).toBe("http://env.test:9000");
    process.env.NEXT_PUBLIC_API_URL = prev;
  });
});

describe("newId", () => {
  it("returns a non-empty string", () => {
    expect(newId().length).toBeGreaterThan(4);
  });
});
