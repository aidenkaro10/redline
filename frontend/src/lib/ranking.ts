/**
 * "Riskiest tomorrow": job sites ordered by tomorrow's 0-10 hazard index, highest first.
 * Sites with no index yet go last. Worker alerts are shown as flags, they don't reorder the list.
 */
import type { Site, SiteStatus } from "./contract.ts";

export function rankSites(sites: Site[], statuses: Record<string, SiteStatus>): Site[] {
  const score = (id: string) => {
    const i = statuses[id]?.index_tomorrow;
    return i === null || i === undefined || !Number.isFinite(i) ? -Infinity : i;
  };
  return sites
    .filter((s) => statuses[s.id])
    .sort((a, b) => score(b.id) - score(a.id) || a.name.localeCompare(b.name));
}
