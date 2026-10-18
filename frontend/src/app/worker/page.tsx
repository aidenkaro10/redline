"use client";

/**
 * Worker app entry: pick who you are (remembered on this phone after the first time).
 * The "Add your crew" QR code opens this with ?team=..., so people only see their own crew.
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
  const { ready } = useLive();
  const { workers, team } = useCrew();
  const teamName = useTeams().find((t) => t.id === team)?.name;
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
    if (!jumped && last && workers.some((w) => w.worker_id === last)) {
      try { sessionStorage.setItem("worker_jumped", "1"); } catch { /* ignore */ }
      router.replace(`/worker/${last}`);
    }
  }, [workers, router]);

  return (
    <div className="font-app mx-auto min-h-screen max-w-xl">
      <AppHeader title="Who are you?" subtitle={teamName ? `Joining ${teamName}. Tap your name.` : undefined} who="Worker" back={back} />
      <main className="px-4 pb-6 pt-2">
        {!ready && <p className="py-10 text-center text-sm text-muted">Connecting…</p>}
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
