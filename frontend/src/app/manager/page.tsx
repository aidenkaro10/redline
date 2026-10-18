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
import { PlanView } from "@/components/PlanView";
import { VideoBar } from "@/components/VideoBar";
import { Avatar, Wash, type WashTone } from "@/components/health";
import { Logo } from "@/components/landing/Hero";
import { shortSiteName, useCrew, useSites } from "@/lib/crew";
import { USING_MOCK, useLive, useSimNow } from "@/lib/live";
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
  const now = useSimNow(1000);

  // Crew tab (who's OK right now) or Plan tab (today's and tomorrow's schedule)
  const [tab, setTab] = useState<"crew" | "plan">("crew");
  const [day, setDay] = useState<"today" | "tomorrow">("today");
  const scene = live.demo?.scene ?? null;
  // Video mode: each scene opens the tab it's about
  useEffect(() => {
    if (scene === "briefing") { setTab("plan"); setDay("tomorrow"); }
    else if (scene === "morning") { setTab("plan"); setDay("today"); }
    else if (scene === "heat" || scene === "lightning" || scene === "sos") setTab("crew");
  }, [scene]);
  const replay = live.demo?.source === "replay";
  // Video mode buttons only show at /manager?video=1, so the normal dashboard stays clean
  const [videoMode, setVideoMode] = useState(false);
  useEffect(() => setVideoMode(new URLSearchParams(window.location.search).has("video")), []);

  return (
    <div className="font-app min-h-screen bg-page text-ink">
      <Wash tone={tone}>
        <header className="mx-auto flex max-w-[1280px] items-center gap-3 whitespace-nowrap px-5 pt-5 md:gap-4 md:px-8 md:pt-6">
          <Link href="/preview" className="flex items-center" aria-label="Redline home"><Logo size={16} /></Link>
          <span className="ml-1 flex items-center gap-2 text-[13px] font-medium text-ink/60" title={live.connected ? (USING_MOCK ? "Demo data" : "Live") : "Reconnecting…"}>
            <span className={`h-1.5 w-1.5 rounded-full ${live.connected ? "bg-ok" : "bg-danger"}`} />
            <span className="hidden sm:inline">{live.connected ? (USING_MOCK ? "Demo data" : "Live") : "Reconnecting…"}</span>
          </span>
          {replay && (
            <span className="hidden rounded-full bg-black/[0.06] px-2.5 py-1 text-[12px] sm:inline font-semibold tabular text-ink-2 dark:bg-white/10" title="Recorded data played back faster than real time">
              Replay · {new Date(now * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}{live.demo!.speed > 1 ? ` · ${live.demo!.speed}x` : ""}
            </span>
          )}
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

      <div className="relative mx-auto flex max-w-[1280px] flex-wrap items-center gap-3 px-4 pt-3 md:px-8">
        <div className="grid grid-cols-2 rounded-[10px] bg-black/[0.06] p-0.5 text-[14px] font-semibold dark:bg-white/10" role="tablist" aria-label="View">
          {(["crew", "plan"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={`min-h-9 rounded-[8px] px-6 py-1.5 capitalize ${tab === t ? "bg-surface text-ink shadow-[0_1px_3px_rgba(0,0,0,0.12)]" : "text-ink-2"}`}>{t}</button>
          ))}
        </div>
        {USING_MOCK && me && videoMode && <VideoBar team={me.team} />}
      </div>

      {tab === "plan" ? (
        <main className="relative mx-auto max-w-[1280px] px-4 pb-16 md:px-8">
          <PlanView day={day} onDay={setDay} workerIds={workers.map((w) => w.worker_id)} />
        </main>
      ) : (
        <main className="relative mx-auto grid max-w-[1280px] gap-x-10 px-4 pb-16 md:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
          <section className="min-w-0"><CrewPanel /></section>
          <section className="min-w-0">
            {me && <div className="pt-4"><JoinQR team={me.team} teamName={me.teamName} /></div>}
            <SitesPanel layout="laptop" />
          </section>
        </main>
      )}
    </div>
  );
}
