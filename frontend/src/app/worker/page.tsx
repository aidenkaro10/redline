"use client";

/** Worker app entry: pick who you are (on a real phone this would be remembered after the first time). */

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { AppHeader } from "@/components/AppHeader";
import { StatusDot } from "@/components/ui";
import { useCrew } from "@/lib/crew";
import { useLive } from "@/lib/live";

export default function WorkerPicker() {
  const { ready } = useLive();
  const { workers } = useCrew();
  const router = useRouter();

  // Same phone as last time: go straight to that worker.
  useEffect(() => {
    let last: string | null = null;
    try { last = localStorage.getItem("worker_id"); } catch { /* private mode */ }
    if (last && workers.some((w) => w.worker_id === last)) router.replace(`/worker/${last}`);
  }, [workers, router]);

  return (
    <div className="mx-auto min-h-screen max-w-xl">
      <AppHeader title="Who are you?" />
      <main className="px-4 py-4">
        {!ready && <p className="py-10 text-center text-sm text-muted">Connecting…</p>}
        <ul className="flex flex-col gap-2">
          {workers.map((w) => (
            <li key={w.worker_id}>
              <Link href={`/worker/${w.worker_id}`}
                onClick={() => { try { localStorage.setItem("worker_id", w.worker_id); } catch { /* ignore */ } }}
                className="flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3.5 font-semibold">
                <StatusDot status={w.status} /> {w.name}
                <span className="ml-auto text-sm font-normal text-muted">{w.role}</span>
              </Link>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
