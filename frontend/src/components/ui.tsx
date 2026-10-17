/**
 * Small shared building blocks: cards, status badges, icons, time formatting.
 */

import type { AlertKind, HazardKind, HazardLevel, Status } from "@/lib/contract";

// ---------------------------------------------------------------------------
// Time
// ---------------------------------------------------------------------------

export function fmtClock(t: number | null | undefined) {
  if (!t) return "--";
  return new Date(t * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export function fmtClockSec(t: number | null | undefined) {
  if (!t) return "--";
  return new Date(t * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

/** 125 -> "2:05" */
export function fmtCountdown(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

export function Card({ title, right, children, className = "", bodyClass = "p-4" }: {
  title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string; bodyClass?: string;
}) {
  return (
    <section className={`flex flex-col rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(0,0,0,0.04)] ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {right && <div className="text-xs text-muted">{right}</div>}
        </header>
      )}
      <div className={`flex-1 ${bodyClass}`}>{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

export const STATUS_TEXT: Record<Status, string> = { green: "OK", yellow: "Warning", red: "Danger" };

const STATUS_CLASS: Record<Status, string> = {
  green: "bg-ok-bg text-ok-ink",
  yellow: "bg-warn-bg text-warn-ink",
  red: "bg-danger-bg text-danger-ink",
};

const STATUS_DOT: Record<Status, string> = { green: "bg-ok", yellow: "bg-warn", red: "bg-danger" };

/** Status is never color alone: dot + icon shape + word. */
export function StatusBadge({ status, big = false }: { status: Status; big?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-semibold ${STATUS_CLASS[status]} ${big ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs"}`}>
      <StatusIcon status={status} className={big ? "h-4 w-4" : "h-3.5 w-3.5"} />
      {STATUS_TEXT[status]}
    </span>
  );
}

export function StatusDot({ status }: { status: Status }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${STATUS_DOT[status]}`} aria-hidden />;
}

export function StatusIcon({ status, className = "h-4 w-4" }: { status: Status; className?: string }) {
  if (status === "green")
    return <svg viewBox="0 0 20 20" className={className} aria-hidden><circle cx="10" cy="10" r="8" fill="currentColor" opacity=".2" /><path d="M6 10.5l2.5 2.5L14 7.5" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (status === "yellow")
    return <svg viewBox="0 0 20 20" className={className} aria-hidden><path d="M10 2.5l8 14H2z" fill="currentColor" opacity=".2" /><path d="M10 8v4M10 14.5v.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>;
  return <svg viewBox="0 0 20 20" className={className} aria-hidden><path d="M7 2h6l5 5v6l-5 5H7l-5-5V7z" fill="currentColor" opacity=".2" /><path d="M10 6v5M10 13.5v.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>;
}

// ---------------------------------------------------------------------------
// Hazards
// ---------------------------------------------------------------------------

export const HAZARD_TEXT_CLASS: Record<HazardKind, string> = {
  heat: "text-heat", lightning: "text-lightning", flood: "text-flood", fire: "text-fire", soil: "text-soil", wildlife: "text-wildlife",
};

export const LEVEL_TEXT: Record<HazardLevel, string> = { none: "Clear", low: "Low", moderate: "Moderate", high: "High", extreme: "Extreme" };

export function LevelPill({ level }: { level: HazardLevel }) {
  const cls = level === "extreme" || level === "high" ? "bg-danger-bg text-danger-ink"
    : level === "moderate" ? "bg-warn-bg text-warn-ink"
      : level === "low" ? "bg-surface-2 text-ink-2" : "bg-ok-bg text-ok-ink";
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${cls}`}>{LEVEL_TEXT[level]}</span>;
}

export function HazardIcon({ kind, className = "h-4 w-4" }: { kind: HazardKind; className?: string }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  switch (kind) {
    case "heat":
      return <svg viewBox="0 0 24 24" className={className} aria-hidden><path {...p} d="M10 14.5V5a2 2 0 114 0v9.5a4 4 0 11-4 0z" /><path {...p} d="M12 9v7" /></svg>;
    case "lightning":
      return <svg viewBox="0 0 24 24" className={className} aria-hidden><path {...p} d="M13 2L5 13h6l-1 9 8-11h-6z" /></svg>;
    case "flood":
      return <svg viewBox="0 0 24 24" className={className} aria-hidden><path {...p} d="M2 9c2 0 2-1.5 4-1.5S8 9 10 9s2-1.5 4-1.5S16 9 18 9s2-1.5 4-1.5M2 14c2 0 2-1.5 4-1.5S8 14 10 14s2-1.5 4-1.5 2 1.5 4 1.5 2-1.5 4-1.5M2 19c2 0 2-1.5 4-1.5S8 19 10 19s2-1.5 4-1.5 2 1.5 4 1.5 2-1.5 4-1.5" /></svg>;
    case "fire":
      return <svg viewBox="0 0 24 24" className={className} aria-hidden><path {...p} d="M12 22c4 0 7-2.7 7-6.5 0-4-3-6-4-9.5-2 1.5-3 3.5-3 5.5-1-1-1.5-2-1.5-3.5C7.5 10 5 12.5 5 15.5 5 19.3 8 22 12 22z" /></svg>;
    case "soil":
      return <svg viewBox="0 0 24 24" className={className} aria-hidden><path {...p} d="M3 8h18M3 13h18M3 18h18" /><circle cx="7" cy="10.5" r=".8" fill="currentColor" /><circle cx="15" cy="15.5" r=".8" fill="currentColor" /></svg>;
    case "wildlife":
      return <svg viewBox="0 0 24 24" className={className} aria-hidden><path {...p} d="M4 17c3 0 3-4 6-4s3 4 6 4 3-3 4-5" /><circle cx="19.5" cy="10.5" r="1.5" {...p} /></svg>;
  }
}

/** Alert kinds mapped to the hazard icon that represents them. */
export function AlertIcon({ kind, className }: { kind: AlertKind; className?: string }) {
  if (kind === "LIGHTNING") return <HazardIcon kind="lightning" className={className} />;
  if (kind === "FLOOD_FIRE") return <HazardIcon kind="flood" className={className} />;
  if (kind === "MANDOWN")
    return <svg viewBox="0 0 24 24" className={className ?? "h-4 w-4"} aria-hidden><circle cx="6" cy="16" r="2" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M9 16h11M13 16l2-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  return <HazardIcon kind="heat" className={className} />;
}

export function alertTone(kind: AlertKind) {
  if (kind === "HEAT_RED" || kind === "MANDOWN") return "text-danger-ink";
  if (kind === "HEAT_YELLOW") return "text-warn-ink";
  if (kind === "LIGHTNING") return "text-lightning";
  return "text-flood";
}

// ---------------------------------------------------------------------------
// 0-10 hazard index: one warm hue, light -> dark (sequential).
// ---------------------------------------------------------------------------

export const INDEX_RAMP = ["#fde3c8", "#f8b98a", "#ec835a", "#d0573a", "#9a2f1c"];

export function indexColor(index: number) {
  return INDEX_RAMP[Math.min(4, Math.floor(index / 2))];
}

export function IndexChip({ index }: { index: number }) {
  const dark = index >= 6;
  return (
    <span className="inline-flex min-w-[2.6rem] justify-center rounded-md px-1.5 py-0.5 text-xs font-bold tabular" style={{ background: indexColor(index), color: dark ? "#fff" : "#3b1a0c" }}>
      {index.toFixed(1)}
    </span>
  );
}
