"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type TunnelJson = { ok: true; url: string; source: string } | { ok: false };

function isLocalHostOrigin(origin: string): boolean {
  try {
    const { hostname } = new URL(origin);
    return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
  } catch {
    return false;
  }
}

export function ClientDemoShareBar() {
  const [pageOrigin, setPageOrigin] = useState("");
  const [tunnel, setTunnel] = useState<TunnelJson | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setPageOrigin(window.location.origin);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/demo-public-url", { cache: "no-store" })
      .then((r) => r.json() as Promise<TunnelJson>)
      .then((j) => {
        if (!cancelled && j && typeof j === "object" && "ok" in j) setTunnel(j);
      })
      .catch(() => {
        if (!cancelled) setTunnel({ ok: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const shareLink = useMemo(() => {
    const base = (tunnel?.ok ? tunnel.url : pageOrigin).replace(/\/$/, "");
    return base ? `${base}/call` : "";
  }, [tunnel, pageOrigin]);

  const copy = useCallback(async () => {
    if (!shareLink) return;
    try {
      await navigator.clipboard.writeText(shareLink);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  }, [shareLink]);

  const showNgrokHint = tunnel?.ok === false && pageOrigin && isLocalHostOrigin(pageOrigin);

  if (!shareLink) return null;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-emerald-900/40 bg-emerald-950/35 px-4 py-2 text-[11px] text-emerald-100/95 md:px-6">
      <span className="font-semibold text-emerald-200/90">Client demo</span>
      <code
        className="max-w-[min(100%,42rem)] truncate rounded bg-black/35 px-2 py-0.5 font-mono text-emerald-100/90"
        title={shareLink}
      >
        {shareLink}
      </code>
      <button
        type="button"
        onClick={() => void copy()}
        className="rounded-md border border-emerald-700/60 bg-emerald-900/40 px-2 py-0.5 font-medium text-emerald-50 hover:bg-emerald-800/50"
      >
        {copied ? "Copied" : "Copy link"}
      </button>
      {tunnel?.ok ? (
        <span className="text-emerald-300/70">
          via {tunnel.source} · refresh after starting the tunnel if this still shows localhost
        </span>
      ) : null}
      {showNgrokHint ? (
        <span className="text-emerald-200/60">
          Tip: run <code className="rounded bg-black/30 px-1">ngrok http 3000</code> then reload —
          we read <code className="rounded bg-black/30 px-1">127.0.0.1:4040</code> for the public
          URL.
        </span>
      ) : null}
    </div>
  );
}
