"use client";

/** Slim top bar for the phone apps: logo, page title, connection dot, light/dark. */

import { USING_MOCK, useLive } from "@/lib/live";
import { ThemeToggle } from "./Nav";

export function AppHeader({ title }: { title: string }) {
  const { connected, demo } = useLive();
  return (
    <header className="sticky top-0 z-[500] flex items-center gap-3 border-b border-line bg-surface/95 px-4 py-3 backdrop-blur">
      <svg viewBox="0 0 28 28" className="h-7 w-7 shrink-0" aria-hidden>
        <rect width="28" height="28" rx="7" fill="var(--accent)" />
        <path d="M8 18c2-6 4-9 6-9s3 5 6 5" stroke="var(--accent-ink)" strokeWidth="2.4" fill="none" strokeLinecap="round" />
        <circle cx="20" cy="14" r="2" fill="var(--accent-ink)" />
      </svg>
      <h1 className="flex-1 text-lg font-bold tracking-tight">{title}</h1>
      {demo?.source === "replay" && <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] font-bold text-accent-ink">REPLAY</span>}
      <span className="flex items-center gap-1.5 text-xs text-ink-2" title={USING_MOCK ? "Mock data" : "Live"}>
        <span className={`h-2 w-2 rounded-full ${connected ? "bg-ok" : "bg-danger"}`} />
        {connected ? (USING_MOCK ? "Mock" : "Live") : "Offline"}
      </span>
      <ThemeToggle />
    </header>
  );
}
