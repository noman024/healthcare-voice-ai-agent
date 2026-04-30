See the **[root README](../README.md)**. From here: `npm install && cp .env.local.example .env.local && npm run dev` — app at [http://localhost:3000/call](http://localhost:3000/call).

The `/call` page is split for maintenance: [`callTypes.ts`](app/call/callTypes.ts) (shared types), [`callUtils.ts`](app/call/callUtils.ts) (feed + chunk helpers), [`audioPlayback.ts`](app/call/audioPlayback.ts) (WAV playback + VAD constants). Env vars are documented in [`.env.local.example`](.env.local.example).
