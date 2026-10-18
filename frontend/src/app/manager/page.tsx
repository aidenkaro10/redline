"use client";

/**
 * Manager view (laptop). The crew lead signs in from the selling page and lands here.
 * Same cards and colors as the phone apps, laid out wide: the crew on the left,
 * the sites map and tomorrow's ranking on the right.
 * Workers use the phone app (/worker): they join by scanning the "Add your crew" QR code.
 * Shows the saved crew picked at sign-in (demo or friends). Demo controls are one click away (top right).
 *
 * Privacy: connects as view=crew, so no raw heart rate or core temp (same as the phone crew app).
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { CrewPanel, SitesPanel } from "@/components/crew-view";
import { JoinQR } from "@/components/JoinQR";
import { Avatar, Wash, type WashTone } from "@/components/health";
import { Logo } from "@/components/landing/Hero";
import { shortSiteName, useCrew, useSites } from "@/lib/crew";
import { USING_MOCK, useLive } from "@/lib/live";
import { type Session, signOut, signedIn } from "@/lib/session";

export default function ManagerPage() {
  const router = useRouter();
  const live = useLive();
  const { workers, siteIds } = useCrew();
  const { byId } = useSites();
  const [me, setMe] = useState<Session | null>(null);
  const who = me?.email ?? null;

  // Not signed in on this laptop yet: go to the sign-in page first.
  useEffect(() => {
    const s = signedIn();
    if (!s) router.replace("/signin");
    else setMe(s);
  }, [router]);

  const attention = workers.filter((w) => w.status !== "green").length;
  const stop = siteIds.some((id) => live.sites[id]?.hazards.some((h) => h.stop_work));
  const downHere = !!live.mandown && workers.some((w) => w.worker_id === live.mandown!.worker_id);   // only this crew's emergencies
  const tone: WashTone = workers.some((w) => w.status === "red") || downHere ? "danger" : stop ? "stop" : attention ? "warn" : "ok";
  const site = byId[siteIds[0]];

  return (
    <div className="font-app min-h-screen bg-page text-ink">
      <Wash tone={tone}>
        <header className="mx-auto flex max-w-[1280px] items-center gap-3 whitespace-nowrap px-5 pt-5 md:gap-4 md:px-8 md:pt-6">
          <Link href="/preview" className="flex items-center" aria-label="Redline home"><Logo size={16} /></Link>
          <span className="ml-1 flex items-center gap-2 text-[13px] font-medium text-ink/60" title={live.connected ? (USING_MOCK ? "Demo data" : "Live") : "Reconnecting…"}>
            <span className={`h-1.5 w-1.5 rounded-full ${live.connected ? "bg-ok" : "bg-danger"}`} />
            <span className="hidden sm:inline">{live.connected ? (USING_MOCK ? "Demo data" : "Live") : "Reconnecting…"}</span>
          </span>
          <nav className="ml-auto flex items-center gap-1 text-[14px] md:gap-2">
            <Link href="/demo" className="rounded-full px-3 py-1.5 font-medium text-ink-2 hover:bg-black/5 dark:hover:bg-white/10">Demo<span className="hidden sm:inline"> controls</span></Link>
            {who && (
              <button onClick={() => { signOut(); router.push("/signin"); }} title={`Signed in as ${who}. Click to sign out.`}
                className="flex items-center gap-2 rounded-full py-1 pl-1 pr-1 font-medium sm:pr-3 text-ink-2 hover:bg-black/5 dark:hover:bg-white/10">
                <Avatar name={who.split("@")[0].replace(/[._-]/g, " ")} /> <span className="hidden sm:inline">Sign out</span>
              </button>
            )}
          </nav>
        </header>
        <div className="mx-auto max-w-[1280px] px-5 pb-2 pt-10 md:px-8 md:pt-14">
          <h1 className="text-[34px] font-semibold md:text-[40px] leading-[1.05] tracking-[-0.035em]">{me?.teamName ?? "Your crew"}</h1>
          <p className="mt-1 text-[17px] text-ink-2">
            {workers.length ? `${site ? `${shortSiteName(site.name)} · ` : ""}${attention ? `${attention} need${attention === 1 ? "s" : ""} attention` : "Everyone OK"} · ${workers.length} on shift` : "Connecting…"}
          </p>
        </div>
      </Wash>

      <main className="relative mx-auto grid max-w-[1280px] gap-x-10 px-4 pb-16 md:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <section className="min-w-0"><CrewPanel /></section>
        <section className="min-w-0">
          {me && <div className="pt-4"><JoinQR team={me.team} teamName={me.teamName} /></div>}
          <SitesPanel layout="laptop" />
        </section>
      </main>
    </div>
  );
}
