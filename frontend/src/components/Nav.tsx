"use client";

/**
 * Top bar on every page: logo, page links, live/replay status, clock, theme toggle.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { USING_MOCK, useLive, useSimNow } from "@/lib/live";
import { fmtClockSec } from "./ui";

const LINKS = [
  { href: "/", label: "Crew lead" },
  { href: "/demo", label: "Demo view" },
  { href: "/worker/worker-1", label: "Worker phone" },
  { href: "/briefing", label: "6 PM briefing" },
  { href: "/report", label: "Report hazard" },
];

export function Nav() {
  const path = usePathname();
  const { connected, demo, ready } = useLive();
  const now = useSimNow(500);
  return (
    <header className="sticky top-0 z-[500] border-b border-line bg-surface/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
        <Link href="/" className="flex items-center gap-2 font-bold tracking-tight">
          <Logo />
          <span>PlanIT Control</span>
        </Link>
        <nav className="order-3 -mx-1 flex w-full gap-1 overflow-x-auto md:order-none md:w-auto">
          {LINKS.map((l) => {
            const active = l.href === "/" ? path === "/" : path.startsWith(l.href.split("/").slice(0, 2).join("/"));
            return (
              <Link key={l.href} href={l.href}
                className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm ${active ? "bg-surface-2 font-semibold text-ink" : "text-ink-2 hover:bg-surface-2"}`}>
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-xs">
          {demo?.source === "replay" && (
            <span className="rounded-md bg-accent px-2 py-1 font-semibold text-accent-ink">REPLAY {demo.speed}x</span>
          )}
          {demo?.demo_threshold && (
            <span className="rounded-md bg-warn-bg px-2 py-1 font-semibold text-warn-ink">DEMO THRESHOLD</span>
          )}
          <span className="hidden tabular text-ink-2 sm:inline">{ready ? fmtClockSec(now) : ""}</span>
          <span className="flex items-center gap-1.5 text-ink-2" title={USING_MOCK ? "Mock backend built into the frontend" : "Real backend"}>
            <span className={`h-2 w-2 rounded-full ${connected ? "bg-ok" : "bg-danger"}`} />
            {connected ? (USING_MOCK ? "Mock data" : "Live") : "Reconnecting…"}
          </span>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

function Logo() {
  return (
    <svg viewBox="0 0 28 28" className="h-7 w-7" aria-hidden>
      <rect width="28" height="28" rx="7" fill="var(--accent)" />
      <path d="M8 18c2-6 4-9 6-9s3 5 6 5" stroke="var(--accent-ink)" strokeWidth="2.4" fill="none" strokeLinecap="round" />
      <circle cx="20" cy="14" r="2" fill="var(--accent-ink)" />
    </svg>
  );
}

/** Light / dark / system. Remembered in this browser only. */
export function ThemeToggle() {
  const [mode, setMode] = useState<"system" | "light" | "dark">("system");
  useEffect(() => {
    try { setMode((localStorage.getItem("theme") as "light" | "dark") || "system"); } catch { /* private mode */ }
  }, []);
  const next = () => {
    const m = mode === "system" ? "dark" : mode === "dark" ? "light" : "system";
    setMode(m);
    try { m === "system" ? localStorage.removeItem("theme") : localStorage.setItem("theme", m); } catch { /* ignore */ }
    const dark = m === "dark" || (m === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
    window.dispatchEvent(new Event("themechange"));
  };
  return (
    <button onClick={next} className="rounded-md border border-line px-2 py-1 text-ink-2 hover:bg-surface-2" title="Switch light / dark / system">
      {mode === "system" ? "Auto" : mode === "dark" ? "Dark" : "Light"}
    </button>
  );
}

/** True when the page is in dark mode (for the map tiles and charts). */
export function useIsDark() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const read = () => setDark(document.documentElement.classList.contains("dark"));
    read();
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onSys = () => {
      let saved: string | null = null;
      try { saved = localStorage.getItem("theme"); } catch { /* ignore */ }
      if (!saved) document.documentElement.classList.toggle("dark", mq.matches);
      read();
    };
    mq.addEventListener("change", onSys);
    window.addEventListener("themechange", read);
    return () => { mq.removeEventListener("change", onSys); window.removeEventListener("themechange", read); };
  }, []);
  return dark;
}
