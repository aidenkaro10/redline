"use client";

/**
 * Crew lead app (phone first), laid out like Apple Health's Summary screen.
 *   Crew  - one card per worker, worst first: status, name, "Danger in N min",
 *           and a ring that empties as danger gets closer. Tap for the details sheet.
 *   Sites - job sites ranked by tomorrow's 0-10 risk index, with a List / Map switch.
 *           Tap a site for its hazards.
 * The soft wash behind the title follows the crew's worst status.
 *
 * Privacy: this page connects as view=crew, so it never receives raw heart
 * rate or core temp. Status, minutes to danger and hazards only.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AppHeader } from "@/components/AppHeader";
import { CrewPanel, SitesPanel } from "@/components/crew-view";
import { Icon, TabBar, Wash, type WashTone } from "@/components/health";
import { useCrew } from "@/lib/crew";
import { useLive } from "@/lib/live";

export default function CrewApp() {
  return <Suspense><CrewAppInner /></Suspense>;
}

function CrewAppInner() {
  const params = useSearchParams();
  const router = useRouter();
  const live = useLive();
  const { workers, siteIds } = useCrew();
  const tab = params.get("tab") === "sites" ? "sites" : "crew";
  const setTab = (t: "crew" | "sites") => router.replace(t === "crew" ? "/" : "/?tab=sites", { scroll: false });

  const attention = workers.filter((w) => w.status !== "green").length;
  const stop = siteIds.some((id) => live.sites[id]?.hazards.some((h) => h.stop_work));
  // The wash follows the worst thing going on: red, then lightning, then yellow, else calm.
  const tone: WashTone = workers.some((w) => w.status === "red") || live.mandown ? "danger" : stop ? "stop" : attention ? "warn" : tab === "sites" ? "neutral" : "ok";
  const subtitle = tab === "sites" ? "Riskiest tomorrow first"
    : workers.length === 0 ? undefined
      : `${attention ? `${attention} need${attention === 1 ? "s" : ""} attention` : "Everyone OK"} · ${workers.length} on shift`;

  return (
    <div className="font-app mx-auto min-h-screen max-w-xl bg-page pb-28">
      <Wash tone={tone}>
        <AppHeader title={tab === "crew" ? "Crew" : "Sites"} subtitle={subtitle} />
      </Wash>
      <main className="relative px-4">{tab === "crew" ? <CrewPanel /> : <SitesPanel />}</main>
      <TabBar items={[
        { label: "Crew", icon: <Icon.people />, active: tab === "crew", onClick: () => setTab("crew") },
        { label: "Sites", icon: <Icon.pin />, active: tab === "sites", onClick: () => setTab("sites") },
      ]} />
    </div>
  );
}
