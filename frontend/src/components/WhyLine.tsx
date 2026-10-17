/**
 * The "why" line under the core temp chart (session H's explanation, e.g.
 * "Rising because heart rate jumped 18 bpm in 10 min and WBGT is 31 °C.").
 * Hidden when there's no explanation for this minute (why is null).
 */
export function WhyLine({ why, tone = "card" }: { why: string | null | undefined; tone?: "card" | "glass" }) {
  if (!why) return null;
  return tone === "glass" ? (
    <p className="mt-2 text-sm opacity-90" data-testid="why">{why}</p>
  ) : (
    <p className="rounded-lg bg-surface-2 px-3 py-2 text-base font-medium" data-testid="why">
      <span className="mr-2 text-xs font-bold uppercase tracking-wider text-muted">Why</span>
      {why}
    </p>
  );
}
