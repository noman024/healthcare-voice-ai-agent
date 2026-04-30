/** @type {import('next').NextConfig} */
const nextConfig = {
  /**
   * Do not customize webpack cache in dev: forcing `memory` was linked to missing
   * `./948.js` chunk errors (broken server bundle graph until `.next` is wiped).
   * If webpack cache ENOENT persists: run `npm run clean` then `npm run dev`.
   */
  /**
   * Proxy FastAPI through Next so one tunnel to :3000 is enough for demos.
   * Prefer `npm run dev` / `npm run dev:demo` for voice WebSockets — `next start`
   * often does not proxy WebSocket upgrades through rewrites.
   */
  async rewrites() {
    const b = "http://127.0.0.1:8000";
    return [
      { source: "/ws/:path*", destination: `${b}/ws/:path*` },
      { source: "/stt", destination: `${b}/stt` },
      { source: "/tts", destination: `${b}/tts` },
      { source: "/process", destination: `${b}/process` },
      { source: "/conversation", destination: `${b}/conversation` },
      { source: "/openapi.json", destination: `${b}/openapi.json` },
      { source: "/docs", destination: `${b}/docs` },
      { source: "/docs/:path*", destination: `${b}/docs/:path*` },
      { source: "/redoc", destination: `${b}/redoc` },
      { source: "/agent/:path*", destination: `${b}/agent/:path*` },
      { source: "/livekit/:path*", destination: `${b}/livekit/:path*` },
      { source: "/avatar/:path*", destination: `${b}/avatar/:path*` },
      { source: "/internal/:path*", destination: `${b}/internal/:path*` },
      { source: "/tools/:path*", destination: `${b}/tools/:path*` },
      { source: "/health", destination: `${b}/health` },
      { source: "/health/:path*", destination: `${b}/health/:path*` },
    ];
  },
};

export default nextConfig;
