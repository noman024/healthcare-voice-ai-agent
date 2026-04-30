import Link from "next/link";

export type CallTopHeaderProps = {
  busy: boolean;
  roomReady: boolean;
};

export function CallTopHeader({ busy, roomReady }: CallTopHeaderProps) {
  return (
    <header className="flex shrink-0 items-center justify-between border-b border-zinc-800/80 px-4 py-3 md:px-6">
      <div className="flex items-center gap-3">
        <div className="h-9 w-9 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 shadow-lg shadow-emerald-900/30" />
        <div>
          <p className="text-sm font-semibold tracking-tight text-white">Healthcare visit</p>
          <p className="text-[11px] text-zinc-500">Text chat or Voice · Live transcript</p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span
          className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
            busy ? "bg-amber-500/20 text-amber-200" : "bg-zinc-800 text-zinc-400"
          }`}
        >
          {busy ? "Working" : !roomReady ? "Starting" : "Ready"}
        </span>
        <Link
          href="/"
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800"
        >
          Exit
        </Link>
      </div>
    </header>
  );
}
