"use client";

/**
 * The 6 PM briefing: tomorrow's 0-10 index and hazards per site, forecast
 * windows, past incidents nearby, and the planner's suggested schedule.
 */

import { Card, HAZARD_TEXT_CLASS, HazardIcon, IndexChip, LevelPill, indexColor } from "@/components/ui";
import type { Briefing, HazardKind, Site } from "@/lib/contract";
import { HAZARD_LABEL } from "@/lib/contract";
import { siteMeta, useCrew } from "@/lib/crew";
import { USING_MOCK, useFetch } from "@/lib/live";

const HOURS = { start: 6, end: 20 };
const WINDOW_COLOR: Partial<Record<HazardKind, string>> = { heat: "var(--heat)", lightning: "var(--lightning)", flood: "var(--flood)", fire: "var(--fire)" };

export default function BriefingPage() {
  const { data: b, error } = useFetch<Briefing>("/briefing");
  const { data: sites } = useFetch<Site[]>("/sites");
  const byId = Object.fromEntries((sites ?? []).map((s) => [s.id, s]));
  const CREW_SITES = new Set(useCrew().siteIds);

  if (error) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-10">
        <p className="text-xs font-bold uppercase tracking-wider text-accent">6 PM briefing</p>
        <h1 className="text-2xl font-bold">No briefing yet</h1>
        <p className="mt-1 text-sm text-ink-2">The planner posts tomorrow&apos;s briefing at 6 PM. Nothing has been posted to this backend yet.</p>
        <p className="mt-3 text-xs text-muted">{error}</p>
      </main>
    );
  }
  if (!b || !sites) return <main className="p-6 text-muted">Loading the briefing…</main>;

  const day = new Date(b.for_date + "T12:00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  const crew = b.sites.filter((s) => CREW_SITES.has(s.site_id));
  const top = b.sites.filter((s) => !CREW_SITES.has(s.site_id));

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-accent">6 PM briefing</p>
          <h1 className="text-3xl font-bold tracking-tight">Tomorrow, {day}</h1>
          <p className="text-sm text-muted">
            Sent {new Date(b.generated_at * 1000).toLocaleString("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" })}
            {" · "}uses only what&apos;s known by 6 PM{USING_MOCK ? " · mock content until the planner (session B) is wired in" : ""}
          </p>
        </div>
        <button onClick={() => window.print()} className="rounded-lg border border-line px-3 py-1.5 text-sm font-semibold hover:bg-surface-2 print:hidden">Print</button>
      </div>

      <p className="rounded-xl border border-line bg-surface p-4 text-lg leading-relaxed">{b.summary}</p>

      <section className="grid gap-4 md:grid-cols-2">
        {crew.map((s) => (
          <Card key={s.site_id} title={byId[s.site_id]?.name ?? s.site_id} right={<span className="flex items-center gap-1.5">Your crew <IndexChip index={s.index} /></span>}>
            <Timeline windows={s.windows} />
            <ul className="mt-3 space-y-1.5">
              {s.top_hazards.length === 0 && <li className="text-sm text-muted">No hazards above low.</li>}
              {s.top_hazards.map((h, i) => (
                <li key={i} className="flex items-center gap-2 text-sm">
                  <span className={HAZARD_TEXT_CLASS[h.kind]}><HazardIcon kind={h.kind} /></span>
                  <span className="flex-1">{HAZARD_LABEL[h.kind]}: {h.text}</span>
                  <LevelPill level={h.level} />
                </li>
              ))}
            </ul>
            {s.past_incidents.length > 0 && (
              <div className="mt-3 border-t border-line pt-2">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted">Past incidents nearby</p>
                {s.past_incidents.map((p, i) => <p key={i} className="text-xs text-ink-2">{p.date}: {p.text} <span className="text-muted">({p.source})</span></p>)}
              </div>
            )}
          </Card>
        ))}
      </section>

      <Card title="Planner's suggested plan for tomorrow">
        <ol className="list-decimal space-y-1.5 pl-5 text-sm">
          {b.schedule_suggestions.map((s) => <li key={s}>{s}</li>)}
        </ol>
        <p className="mt-2 text-xs text-muted">Every plan passes the safety rule checker (code). The AI suggests; it never sets safety limits.</p>
      </Card>

      <Card title="Highest-risk sites tomorrow" right="0-10 hazard index" bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="px-4 py-2 font-medium">#</th>
                <th className="px-2 py-2 font-medium">Site</th>
                <th className="px-2 py-2 font-medium">Index</th>
                <th className="px-2 py-2 font-medium">Main hazards</th>
                <th className="px-4 py-2 font-medium">Windows</th>
              </tr>
            </thead>
            <tbody>
              {top.map((s, i) => (
                <tr key={s.site_id} className="border-b border-line last:border-0">
                  <td className="px-4 py-2 text-muted tabular">{i + 1}</td>
                  <td className="px-2 py-2">
                    <p className="font-medium">{byId[s.site_id]?.name}</p>
                    <p className="text-xs text-muted">{siteMeta(byId[s.site_id], false)}</p>
                  </td>
                  <td className="px-2 py-2">
                    <div className="flex items-center gap-2">
                      <IndexChip index={s.index} />
                      <span className="hidden h-2 w-20 overflow-hidden rounded-full bg-surface-2 sm:block">
                        <span className="block h-full rounded-full" style={{ width: `${s.index * 10}%`, background: indexColor(s.index) }} />
                      </span>
                    </div>
                  </td>
                  <td className="px-2 py-2 text-xs text-ink-2">{s.top_hazards.map((h) => h.text).join(" · ") || "--"}</td>
                  <td className="px-4 py-2 text-xs text-ink-2">{s.windows.map((w) => w.text).join(" · ") || "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </main>
  );
}

/** Hour strip, 6 AM to 8 PM, with the hazard windows drawn on it. */
function Timeline({ windows }: { windows: Briefing["sites"][number]["windows"] }) {
  const span = HOURS.end - HOURS.start;
  const pct = (h: number) => ((h - HOURS.start) / span) * 100;
  return (
    <div>
      <div className="relative h-auto space-y-1">
        {windows.length === 0 && <p className="text-[11px] text-muted">No hazard windows forecast</p>}
        {windows.map((w, i) => (
          <div key={i}>
            <p className="text-[11px] font-semibold text-ink-2">{w.text}</p>
            <div className="relative h-2.5 rounded-full bg-surface-2">
              <div className="absolute inset-y-0 rounded-full" style={{ left: `${pct(w.start_hour)}%`, width: `${pct(w.end_hour) - pct(w.start_hour)}%`, background: WINDOW_COLOR[w.kind] ?? "var(--muted)" }} />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted tabular">
        {[6, 9, 12, 15, 18].map((h) => <span key={h}>{h === 12 ? "12 PM" : h > 12 ? `${h - 12} PM` : `${h} AM`}</span>)}
        <span>8 PM</span>
      </div>
    </div>
  );
}
