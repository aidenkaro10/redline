"use client";

/**
 * Worker app entry. With ?team=... (the "Add your crew" QR code / link): pick who you are.
 * Without it (the plain "Worker app" link): pick your crew first.
 * Your pick is remembered on this phone, and you can always come back here to switch.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AppHeader } from "@/components/AppHeader";
import { Chevron } from "@/components/Sheet";
import { StatusDot } from "@/components/ui";
import { useCrew, useTeams } from "@/lib/crew";
import { useLive } from "@/lib/live";
import { signedIn } from "@/lib/session";

export default function WorkerPicker() {
  const { ready, workers: everyone } = useLive();
  const { workers, team } = useCrew();
  const teams = useTeams();
  const teamName = teams.find((t) => t.id === team)?.name;
  // Did the link say which crew? (null until we've looked at the address)
  const [teamInLink, setTeamInLink] = useState<boolean | null>(null);
  useEffect(() => setTeamInLink(!!new URLSearchParams(window.location.search).get("team")), []);
  const router = useRouter();
  // Back goes to the manager dashboard if this browser is signed in as a manager, else the home page.
  const [back, setBack] = useState({ href: "/preview", label: "Home" });
  useEffect(() => { if (signedIn()) setBack({ href: "/manager", label: "Dashboard" }); }, []);

  // Same phone as last time: go straight to that worker, but only once per browser tab, and never
  // when they came back here on purpose (the back button / "switch" link adds ?switch=1).
  // Otherwise pressing Back would bounce them straight forward again.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("switch")) return;
    let last: string | null = null, jumped = false;
    try { last = localStorage.getItem("worker_id"); jumped = sessionStorage.getItem("worker_jumped") === "1"; } catch { /* private mode */ }
    if (teamInLink === null) return;
    const known = teamInLink ? workers.some((w) => w.worker_id === last) : !!(last && everyone[last]);
    if (!jumped && last && known) {
      try { sessionStorage.setItem("worker_jumped", "1"); } catch { /* ignore */ }
      router.replace(`/worker/${last}`);
    }
  }, [workers, everyone, teamInLink, router]);

  // Plain link: pick your crew first (each opens that crew's name list)
  if (teamInLink === false) {
    return (
      <div className="font-app mx-auto min-h-screen max-w-xl">
        <AppHeader title="Pick your crew" subtitle="Your crew lead can also send you a link or a QR code." who="Worker" back={back} />
        <main className="px-4 pb-6 pt-2">
          <ul className="divide-y divide-line overflow-hidden rounded-2xl bg-surface">
            {teams.map((t) => (
              <li key={t.id}>
                {/* a full page load, so the crew in the link is picked up everywhere */}
                <a href={`/worker?team=${encodeURIComponent(t.id)}`} className="flex min-h-14 items-center gap-3 px-4 py-3 text-[16px] font-semibold active:bg-surface-2">
                  <span className="shrink-0">{t.name}</span>
                  <span className="ml-auto min-w-0 truncate text-[14px] font-normal text-muted">{t.id === "demo" ? "Made-up crew" : "Real crew"}</span>
                  <Chevron className="h-3.5 w-3.5 text-muted/70" />
                </a>
              </li>
            ))}
          </ul>
        </main>
      </div>
    );
  }

  return (
    <div className="font-app mx-auto min-h-screen max-w-xl">
      <AppHeader title="Who are you?" subtitle={teamName ? `Joining ${teamName}. Tap your name.` : undefined} who="Worker" back={back} />
      <main className="px-4 pb-6 pt-2">
        {(!ready || teamInLink === null) && <p className="py-10 text-center text-sm text-muted">Connecting…</p>}
        <ul className="divide-y divide-line overflow-hidden rounded-2xl bg-surface">
          {workers.map((w) => (
            <li key={w.worker_id}>
              <Link href={`/worker/${w.worker_id}`}
                onClick={() => { try { localStorage.setItem("worker_id", w.worker_id); sessionStorage.setItem("worker_jumped", "1"); } catch { /* ignore */ } }}
                className="flex min-h-14 items-center gap-3 px-4 py-3 text-[16px] font-semibold active:bg-surface-2">
                <StatusDot status={w.status} /> <span className="shrink-0">{w.name}</span>
                <span className="ml-auto min-w-0 truncate text-[14px] font-normal text-muted">{w.role}</span>
                <Chevron className="h-3.5 w-3.5 text-muted/70" />
              </Link>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
