import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/** Ngrok agent local API (default when you run `ngrok http 3000`). */
const NGROK_TUNNELS = process.env.NGROK_LOCAL_API_URL ?? "http://127.0.0.1:4040/api/tunnels";

type NgrokTunnel = { public_url?: string; proto?: string };

type OkBody = { ok: true; url: string; source: "ngrok" };
type NoBody = { ok: false };

/**
 * Returns a public https URL when ngrok is running and forwarding to this machine.
 * The browser cannot call port 4040 from a remote origin (CORS); this route runs on the Next server.
 */
export async function GET(): Promise<NextResponse<OkBody | NoBody>> {
  try {
    const res = await fetch(NGROK_TUNNELS, {
      cache: "no-store",
      signal: AbortSignal.timeout(1200),
    });
    if (!res.ok) return NextResponse.json({ ok: false });
    const data = (await res.json()) as { tunnels?: NgrokTunnel[] };
    const tunnels = data.tunnels ?? [];
    const https =
      tunnels.find((t) => (t.public_url ?? "").startsWith("https://")) ??
      tunnels.find((t) => t.proto === "https");
    const pick = https ?? tunnels[0];
    const raw = pick?.public_url?.trim().replace(/\/$/, "");
    if (raw && /^https?:\/\//i.test(raw)) {
      return NextResponse.json({ ok: true, url: raw, source: "ngrok" });
    }
  } catch {
    /* ngrok not running or unreachable */
  }
  return NextResponse.json({ ok: false });
}
