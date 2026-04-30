import type { AgentPayload, FeedItem } from "./callTypes";

/** When `NEXT_PUBLIC_API_URL` is unset, `/call` uses the page origin and Next rewrites to FastAPI (:8000). */
export function resolveClientApiBase(): string {
  const fromEnv = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/$/, "");
  if (fromEnv) return fromEnv;
  if (typeof window !== "undefined") return window.location.origin;
  return "";
}

export function httpToWsBase(httpUrl: string): string {
  const t = httpUrl.trim().replace(/\/$/, "");
  if (!t) {
    if (typeof window !== "undefined") {
      return window.location.origin.replace(/^http/, "ws");
    }
    return "ws://127.0.0.1:3000";
  }
  return t.replace(/^http/, "ws");
}

/** Reassemble base64-encoded byte chunks from the LiveKit worker into a WAV ``Uint8Array``. */
export function mergeTtsWavChunks(parts: Map<number, string>): Uint8Array {
  const keys = [...parts.keys()].sort((a, b) => a - b);
  const chunks: Uint8Array[] = [];
  for (const k of keys) {
    const b64 = parts.get(k);
    if (!b64) continue;
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    chunks.push(u8);
  }
  const total = chunks.reduce((s, c) => s + c.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const c of chunks) {
    out.set(c, o);
    o += c.length;
  }
  return out;
}

/** Accept JSON where numeric fields may arrive as strings after relay/parse. */
export function coerceVaSeq(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return -1;
}

export function coerceVaLast(v: unknown): boolean {
  return v === true || v === "true" || v === 1 || v === "1";
}

export function coerceOffsetMs(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return 0;
}

export function coerceNonNegInt(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v) && v >= 0) return Math.floor(v);
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n) && n >= 0) return Math.floor(n);
  }
  return null;
}

/** Short label for planner/tool names (status bar + activity feed). */
export function toolIntentLabel(tool: unknown): string {
  const t = typeof tool === "string" ? tool : "";
  const labels: Record<string, string> = {
    none: "Thinking…",
    identify_user: "Identifying patient",
    fetch_slots: "Loading availability",
    book_appointment: "Booking appointment",
    retrieve_appointments: "Loading appointments",
    cancel_appointment: "Cancelling appointment",
    modify_appointment: "Updating appointment",
    end_conversation: "Ending visit",
  };
  return labels[t] ?? (t ? t : "Working…");
}

/**
 * After ``identify_user``, the backend may send ``session_identity`` (REST/WebSocket ``done``)
 * or only ``tool_execution`` (LiveKit worker data). Same normalized phone must drive
 * ``session_id`` on the client so WebSocket ↔ LiveKit ↔ text share one tool session.
 */
export function suggestedSessionIdFromResult(result: AgentPayload | null): string | null {
  if (!result) return null;
  const si = result["session_identity"] as { suggested_session_id?: string } | undefined;
  const fromSi = typeof si?.suggested_session_id === "string" ? si.suggested_session_id.trim() : "";
  if (fromSi) return fromSi;
  const te = result.tool_execution;
  if (!te || typeof te !== "object") return null;
  const t = te as Record<string, unknown>;
  if (t.success !== true || t.tool !== "identify_user") return null;
  const data = t.data as Record<string, unknown> | undefined;
  const phone = data && typeof data.phone === "string" ? data.phone.trim() : "";
  return phone || null;
}

/** One-line status for the header while tools run or complete (including LiveKit worker payloads). */
export function toolExecutionStatusBanner(te: unknown): string | null {
  if (!te || typeof te !== "object") return null;
  const o = te as Record<string, unknown>;
  const tool = typeof o.tool === "string" ? o.tool : "";
  if (!tool) return null;
  const phase = typeof o.phase === "string" ? o.phase : "";
  if (phase === "running") return `${toolIntentLabel(tool)}…`;
  if (o.success === true) return `${toolIntentLabel(tool)} · OK`;
  const errRaw = o.error as Record<string, unknown> | undefined;
  const errMsg = typeof errRaw?.message === "string" ? errRaw.message : "Failed";
  return `${toolIntentLabel(tool)} · ${errMsg}`;
}

export function buildActivityFeed(result: AgentPayload): FeedItem[] {
  const out: FeedItem[] = [];
  const planRaw = result.plan;
  const plan = planRaw && typeof planRaw === "object" ? (planRaw as Record<string, unknown>) : null;
  const intent =
    typeof plan?.intent === "string"
      ? plan.intent
      : typeof result.intent === "string"
        ? result.intent
        : "";
  if (intent) out.push({ id: "intent", label: intent, tone: "neutral" });
  const tool = typeof plan?.tool === "string" ? plan.tool : "";
  const teRaw = result.tool_execution;
  /** When tool_execution names the same tool as plan, show only execution lines (running / done) — matches LiveKit worker payloads and avoids duplicate labels. */
  let skipPlanToolLine = false;
  if (teRaw && typeof teRaw === "object" && tool && tool !== "none") {
    const te0 = teRaw as Record<string, unknown>;
    const teT = typeof te0.tool === "string" ? te0.tool : "";
    if (teT === tool) skipPlanToolLine = true;
  }
  if (tool && tool !== "none" && !skipPlanToolLine) {
    out.push({ id: "tool-plan", label: toolIntentLabel(tool), tone: "neutral" });
  }
  if (teRaw && typeof teRaw === "object") {
    const te = teRaw as Record<string, unknown>;
    const phase = typeof te.phase === "string" ? te.phase : "";
    const ok = te.success === true;
    const errMsg =
      typeof (te.error as Record<string, unknown> | undefined)?.message === "string"
        ? String((te.error as { message?: string }).message)
        : "Failed";
    const toolTag = typeof te.tool === "string" ? te.tool : tool;
    if (phase === "running" && toolTag) {
      out.push({
        id: "tool-running",
        label: `${toolIntentLabel(toolTag)}…`,
        tone: "neutral",
      });
    } else if (toolTag) {
      out.push({
        id: "tool-done",
        label: ok ? `${toolIntentLabel(toolTag)} · OK` : `${toolIntentLabel(toolTag)} · ${errMsg}`,
        tone: ok ? "ok" : "warn",
      });
    }
  }
  const warn = typeof result.warning === "string" ? result.warning : "";
  if (warn) out.push({ id: "warn", label: warn, tone: "warn" });
  return out;
}

export function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}
