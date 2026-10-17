"use client";

/**
 * Header for the phone apps, Apple Health style: a small status line on top
 * (live / mock / reconnecting, replay, initials), then a big title. Light/dark follows the phone.
 * Sits on top of a Wash (the soft color wash), so it has no background of its own.
 */

import { USING_MOCK, useLive } from "@/lib/live";
import { Avatar } from "./health";

export function AppHeader({ title, subtitle, who = "Crew Lead" }: { title: string; subtitle?: React.ReactNode; who?: string }) {
  const { connected, demo } = useLive();
  return (
    <header className="px-4 pb-1 pt-[max(12px,env(safe-area-inset-top))]">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 whitespace-nowrap text-[13px] font-medium text-ink/60" title={USING_MOCK ? "Mock data" : "Live"}>
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-ok" : "bg-danger"}`} />
          {connected ? (USING_MOCK ? "Mock data" : "Live") : "Reconnecting…"}
          {demo?.source === "replay" && <span className="rounded-full bg-white/60 px-2 py-0.5 text-[11px] font-semibold text-ink/70 dark:bg-white/10">Replay {demo.speed}x</span>}
        </span>
        <Avatar name={who} />
      </div>
      <h1 className="mt-10 text-[34px] font-semibold leading-[1.08] tracking-[-0.035em]">{title}</h1>
      {subtitle && <p className="mt-0.5 text-[17px] text-ink-2">{subtitle}</p>}
    </header>
  );
}
