import Link from "next/link";

const defaultApiUrl = "http://localhost:8000";

type HealthPayload = { status: string };

async function fetchHealth(baseUrl: string): Promise<{
  ok: boolean;
  data?: HealthPayload;
  error?: string;
}> {
  const url = `${baseUrl.replace(/\/$/, "")}/health`;
  try {
    const res = await fetch(url, {
      cache: "no-store",
    });
    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status}` };
    }
    const data = (await res.json()) as HealthPayload;
    return { ok: true, data };
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return { ok: false, error: message };
  }
}

export default async function Home() {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? defaultApiUrl;
  const health = await fetchHealth(baseUrl);

  return (
    <div className="flex min-h-full flex-1 flex-col items-center justify-center bg-zinc-50 px-6 py-16 font-sans dark:bg-zinc-950">
      <main className="w-full max-w-lg rounded-2xl border border-zinc-200 bg-white p-8 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <p className="text-sm font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Voice healthcare agent
        </p>
        <h1 className="mt-2 text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
          Backend connectivity
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
          Verifying <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs dark:bg-zinc-800">{baseUrl}/health</code>.
        </p>

        <div
          className={`mt-6 rounded-xl border px-4 py-3 text-sm ${
            health.ok
              ? "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-950/40 dark:text-emerald-200"
              : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-200"
          }`}
        >
          {health.ok && health.data ? (
            <>
              <span className="font-medium">API healthy</span>
              <span className="mt-1 block font-mono text-xs opacity-90">
                {JSON.stringify(health.data)}
              </span>
            </>
          ) : (
            <>
              <span className="font-medium">Could not reach API</span>
              <span className="mt-1 block text-xs opacity-90">
                {health.error ?? "Start the FastAPI server (see repo README)."}
              </span>
            </>
          )}
        </div>

        <ul className="mt-6 flex flex-col gap-2 text-sm text-zinc-600 dark:text-zinc-400">
          <li>
            <Link
              href="/call"
              className="font-medium text-zinc-900 underline decoration-zinc-300 underline-offset-2 hover:decoration-zinc-600 dark:text-zinc-100 dark:decoration-zinc-600 dark:hover:decoration-zinc-400"
            >
              Call lab (Phase 7)
            </Link>
            <span className="text-zinc-400"> — text + audio file → agent</span>
          </li>
          <li>
            <Link
              href={`${baseUrl.replace(/\/$/, "")}/docs`}
              className="font-medium text-zinc-900 underline decoration-zinc-300 underline-offset-2 hover:decoration-zinc-600 dark:text-zinc-100 dark:decoration-zinc-600 dark:hover:decoration-zinc-400"
            >
              OpenAPI docs
            </Link>
            <span className="text-zinc-400"> — backend Swagger UI</span>
          </li>
          <li>
            <span>
              Set <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs dark:bg-zinc-800">NEXT_PUBLIC_API_URL</code> in{" "}
              <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs dark:bg-zinc-800">.env.local</code> if the API
              is not on {defaultApiUrl}.
            </span>
          </li>
        </ul>
      </main>
    </div>
  );
}
