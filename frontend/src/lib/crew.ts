"use client";

/**
 * Helpers so no page hardcodes worker or site ids. The mock uses "worker-1" and
 * "fl-deerhaven"; session A's backend uses "demo-1" and "eia-663" / "site-gnv".
 * Everything here comes from whatever backend we're connected to.
 */

import { usePathname } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import type { Site, WorkerStatus } from "./contract";
import { apiGet, useLive } from "./live";
import { signedIn } from "./session";

// The site list rarely changes, so every page shares one copy.
let sitesCache: Promise<Site[]> | null = null;
let sitesLoaded: Site[] | null = null;      // the same list once it has arrived (used for the first render)

/** Tests (and anything that already has the list) can hand it over directly. */
export function primeSites(list: Site[]) {
  sitesLoaded = list;
  sitesCache = Promise.resolve(list);
}

export function useSites() {
  const [sites, setSites] = useState<Site[] | null>(() => sitesLoaded);
  useEffect(() => {
    let alive = true;
    sitesCache ??= apiGet<Site[]>("/sites").then((s) => { sitesLoaded = s; return s; }).catch((e) => { sitesCache = null; throw e; });
    sitesCache.then((s) => alive && setSites(s)).catch(() => alive && setSites([]));
    return () => { alive = false; };
  }, []);
  const byId = useMemo(() => Object.fromEntries((sites ?? []).map((s) => [s.id, s])) as Record<string, Site>, [sites]);
  return { sites, byId };
}

/**
 * Which saved crew this screen shows: ?team=... in the link (the QR join link), the signed-in crew
 * on the manager view, otherwise the demo crew. Workers with no team are in the demo crew.
 */
export function useTeam(): string {
  const path = usePathname();
  const [team, setTeam] = useState("demo");
  useEffect(() => {
    const fromLink = new URLSearchParams(window.location.search).get("team");
    setTeam(fromLink || (path.startsWith("/manager") ? signedIn()?.team : null) || "demo");
  }, [path]);
  return team;
}

export interface TeamInfo { id: string; name: string; locked: boolean }
const FALLBACK_TEAMS: TeamInfo[] = [
  { id: "demo", name: "Demo crew", locked: false },
  { id: "friends", name: "Friends crew", locked: true },
];
let teamsCache: Promise<TeamInfo[]> | null = null;

/** The two saved crews and their names (from the server, so the friends crew's name can live in Render settings). */
export function useTeams(): TeamInfo[] {
  const [list, setList] = useState<TeamInfo[]>(FALLBACK_TEAMS);
  useEffect(() => {
    let alive = true;
    teamsCache ??= fetch("/api/teams").then((r) => (r.ok ? r.json() : FALLBACK_TEAMS)).catch(() => FALLBACK_TEAMS);
    teamsCache.then((t) => alive && Array.isArray(t) && setList(t));
    return () => { alive = false; };
  }, []);
  return list;
}

/** The crew on this screen: its workers sorted by id, the sites they're on, and the "demo wearer" (first worker). */
export function useCrew() {
  const { workers } = useLive();
  const team = useTeam();
  return useMemo(() => {
    const list: WorkerStatus[] = Object.values(workers)
      .filter((w) => (w.team ?? "demo") === team)
      .sort((a, b) => a.worker_id.localeCompare(b.worker_id, undefined, { numeric: true }));
    const siteIds = [...new Set(list.map((w) => w.site_id))];
    return { workers: list, siteIds, firstWorkerId: list[0]?.worker_id ?? null, team };
  }, [workers, team]);
}

/** "Deerhaven Generating Station" -> "Deerhaven" for tight spots. */
export function shortSiteName(name: string | undefined) {
  return (name ?? "").replace(/ (Generating|Power|Energy) (Station|Center|Plant|Complex)$/, "");
}

/** "GRU · gas · 450 MW · Alachua Co." without blanks when a backend leaves fields empty. */
export function siteMeta(s: Pick<Site, "operator" | "tech" | "capacity_mw" | "county"> | undefined, withOperator = true) {
  if (!s) return "";
  return [withOperator ? s.operator : "", s.tech, s.capacity_mw ? `${s.capacity_mw.toLocaleString()} MW` : "", s.county ? `${s.county} Co.` : ""]
    .filter(Boolean).join(" · ");
}
