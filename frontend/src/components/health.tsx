"use client";

/**
 * Building blocks for the phone apps, modeled on Apple Health's Summary screen
 * (the team's pick from the UI board):
 *   - Wash: a soft two-color wash behind the big title that fades into the page
 *   - Card: colored icon + label, time + chevron, one big number with a small gray unit,
 *           and a tiny chart on the right
 *   - Ring / Bars / Spark: the tiny charts
 *   - TabBar: the floating glass pill at the bottom
 */

import Link from "next/link";

// ---------------------------------------------------------------------------
// Colors (Apple system colors). Each card has its own category color, like Health.
// ---------------------------------------------------------------------------

export const HUE = {
  core: "#ff2d55",       // core temp: Health's heart pink-red
  break: "#30b0c7",      // breaks: teal
  ok: "#34c759",
  warn: "#ff9500",
  danger: "#ff3b30",
  heat: "#ff9500",
  lightning: "#5856d6",
  flood: "#32ade6",
  fire: "#ff6b2c",
  soil: "#a2845e",
  wildlife: "#34c759",
  sos: "#af52de",
  crew: "#007aff",
  site: "#ff6b2c",
} as const;

/** The wash colors per mood. Calm mint/sky when OK, warmer as things get worse. */
const WASH: Record<string, [string, string]> = {
  ok: ["#a8e6c6", "#bcd5f8"],
  warn: ["#ffd08a", "#ffb4a2"],
  danger: ["#ff9c9c", "#f6a6cf"],
  stop: ["#b6b2ff", "#dcbcff"],
  neutral: ["#f7b3c4", "#c6b6f7"],     // Health's own pink to lavender
};

export type WashTone = keyof typeof WASH;

/** Big title area with the color wash behind it. The wash fades into the page color. */
export function Wash({ tone, children }: { tone: WashTone; children: React.ReactNode }) {
  const [a, b] = WASH[tone] ?? WASH.neutral;
  return (
    <div className="relative">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-[380px] opacity-100 transition-[background] duration-700 dark:opacity-50"
        style={{ background: `linear-gradient(to bottom, transparent 25%, var(--page) 92%), linear-gradient(115deg, ${a} 0%, ${b} 100%)` }} />
      <div className="relative">{children}</div>
    </div>
  );
}

/** Round initials in the top right, where Health puts your photo. */
export function Avatar({ name }: { name: string }) {
  const initials = name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  return (
    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white/70 text-[13px] font-semibold text-ink shadow-[0_1px_2px_rgba(0,0,0,0.08)] backdrop-blur dark:bg-white/15" aria-hidden>
      {initials}
    </span>
  );
}

export function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between px-1 pb-2 pt-5">
      <h2 className="text-[22px] font-bold tracking-tight">{title}</h2>
      {action && <span className="text-[17px] text-accent">{action}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

type CardProps = {
  icon: React.ReactNode;
  label: React.ReactNode;
  color: string;
  meta?: React.ReactNode;           // top right, gray (a time, "Today")
  viz?: React.ReactNode;            // tiny chart, bottom right
  onClick?: () => void;
  href?: string;
  children: React.ReactNode;        // the big value and its sub line
  className?: string;
};

/** One Health-style card. Taps open more (a sheet or another page). */
export function Card({ icon, label, color, meta, viz, onClick, href, children, className = "" }: CardProps) {
  const inner = (
    <>
      <span className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-1.5 text-[15px] font-semibold" style={{ color }}>
          <span className="h-4 w-4 shrink-0">{icon}</span>
          <span className="truncate">{label}</span>
        </span>
        <span className="flex shrink-0 items-center gap-1 text-[15px] text-muted">
          {meta}
          {(onClick || href) && <Chev />}
        </span>
      </span>
      <span className="mt-5 flex items-end justify-between gap-3">
        <span className="block min-w-0">{children}</span>
        {viz && <span className="shrink-0">{viz}</span>}
      </span>
    </>
  );
  const cls = `block w-full rounded-[14px] bg-surface px-4 py-3.5 text-left ${onClick || href ? "transition-transform active:scale-[0.985]" : ""} ${className}`;
  if (href) return <Link href={href} className={cls}>{inner}</Link>;
  if (onClick) return <button onClick={onClick} className={cls}>{inner}</button>;
  return <div className={cls}>{inner}</div>;
}

/** Big bold number with a small gray unit: "1,367 steps", "37.6 °C". */
export function Value({ children, unit, pre, tone }: { children: React.ReactNode; unit?: string; pre?: string; tone?: string }) {
  return (
    <span className="block leading-none">
      {pre && <span className="mr-1 text-[17px] font-semibold text-muted">{pre}</span>}
      <span className="text-[30px] font-bold tracking-[-0.02em] tabular" style={tone ? { color: tone } : undefined}>{children}</span>
      {unit && <span className="ml-1 text-[17px] font-semibold text-muted">{unit}</span>}
    </span>
  );
}

export function Sub({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return <span className="mt-1.5 block truncate text-[15px] text-muted" style={tone ? { color: tone } : undefined}>{children}</span>;
}

// ---------------------------------------------------------------------------
// Tiny charts
// ---------------------------------------------------------------------------

/** Donut with a number in the middle (Health's sleep score). value 0..1 fills the ring. */
export function Ring({ value, color, label, size = 44 }: { value: number; color: string; label?: React.ReactNode; size?: number }) {
  const r = 15.5, c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value));
  return (
    <span className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg viewBox="0 0 40 40" className="absolute inset-0 -rotate-90" aria-hidden>
        <circle cx="20" cy="20" r={r} fill="none" stroke="currentColor" strokeOpacity=".1" strokeWidth="5" />
        <circle cx="20" cy="20" r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" strokeDasharray={`${v * c} ${c}`} />
      </svg>
      {label !== undefined && <span className="relative text-[12px] font-bold tabular">{label}</span>}
    </span>
  );
}

/** Small gray bars with the last one in color (Health's steps card). */
export function Bars({ values, color, max }: { values: number[]; color: string; max?: number }) {
  const top = max ?? Math.max(1, ...values);
  return (
    <span className="flex h-9 items-end gap-[3px]" aria-hidden>
      {values.map((v, i) => (
        <span key={i} className="w-[5px] rounded-[1.5px]"
          style={{ height: `${Math.max(8, (v / top) * 100)}%`, background: i === values.length - 1 ? color : "color-mix(in srgb, currentColor 14%, transparent)" }} />
      ))}
    </span>
  );
}

/** Tiny line with a dot at the end (now). Past solid, forecast dashed. */
export function Spark({ past, future = [], color, lo, hi }: { past: number[]; future?: number[]; color: string; lo?: number; hi?: number }) {
  const all = [...past, ...future];
  if (all.length < 2) return null;
  const min = lo ?? Math.min(...all), max = hi ?? Math.max(...all);
  const w = 64, h = 34, n = all.length;
  const x = (i: number) => (i / (n - 1)) * (w - 4) + 2;
  const y = (v: number) => h - 3 - ((v - min) / Math.max(0.01, max - min)) * (h - 6);
  const path = (vals: number[], off: number) => vals.map((v, i) => `${i ? "L" : "M"}${x(i + off).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const nowI = past.length - 1;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-[34px] w-16" aria-hidden>
      <path d={path(past, 0)} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {future.length > 0 && <path d={path([past[nowI], ...future], nowI)} fill="none" stroke={color} strokeOpacity=".45" strokeWidth="2" strokeDasharray="2.5 3" strokeLinecap="round" />}
      <circle cx={x(nowI)} cy={y(past[nowI])} r="3" fill={color} stroke="var(--surface)" strokeWidth="1.5" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Floating tab bar (iOS 26 style glass pill)
// ---------------------------------------------------------------------------

export function TabBar({ items }: { items: { label: string; icon: React.ReactNode; active: boolean; onClick: () => void }[] }) {
  return (
    <nav className="pointer-events-none fixed inset-x-0 bottom-0 z-[600] flex justify-center pb-[max(14px,env(safe-area-inset-bottom))]">
      <div className="pointer-events-auto flex gap-1 rounded-full border border-white/70 bg-white/72 p-1 shadow-[0_8px_30px_rgba(0,0,0,0.12)] backdrop-blur-xl dark:border-white/10 dark:bg-[#1c1c1e]/80">
        {items.map((t) => (
          <button key={t.label} onClick={t.onClick} aria-current={t.active ? "page" : undefined}
            className={`flex min-w-[84px] flex-col items-center gap-0.5 rounded-full px-5 py-1.5 text-[11px] font-semibold ${t.active ? "bg-black/[0.06] text-accent dark:bg-white/10" : "text-ink-2"}`}>
            <span className="h-[22px] w-[22px]">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Filled icons (SF Symbols-like, drawn on a 24 grid)
// ---------------------------------------------------------------------------

const I = ({ children }: { children: React.ReactNode }) => <svg viewBox="0 0 24 24" className="h-full w-full" fill="currentColor" aria-hidden>{children}</svg>;

export const Icon = {
  thermo: () => <I><path d="M9 4.5a3 3 0 016 0v8.1a5 5 0 11-6 0zm3-1.3a1.3 1.3 0 00-1.3 1.3v8.9l-.4.3a3.3 3.3 0 103.4 0l-.4-.3V4.5A1.3 1.3 0 0012 3.2z" /><path d="M11.2 9h1.6v5.6a2.2 2.2 0 11-1.6 0z" /></I>,
  cup: () => <I><path d="M4 7h12.5v6.5a5 5 0 01-5 5h-2.5a5 5 0 01-5-5zm12.5 1.5h1.2a2.8 2.8 0 010 5.6h-1.4c.13-.5.2-1 .2-1.6v-.1h1.2a1.2 1.2 0 000-2.4h-1.2zM3 20h15v1.6H3z" /><path d="M8 2.5c.9.9.9 1.9 0 2.8M11.5 2.5c.9.9.9 1.9 0 2.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" fill="none" /></I>,
  bolt: () => <I><path d="M13.5 1.8L4.6 13.2c-.4.5 0 1.3.6 1.3h5.3l-1.4 7.4c-.1.6.7 1 1.1.5l9.2-11.6c.4-.5 0-1.3-.6-1.3h-5.4l1.3-7.2c.1-.6-.7-1-1.2-.5z" /></I>,
  flame: () => <I><path d="M12 22c4.3 0 7.4-2.9 7.4-7 0-3.4-2-5.7-3.6-7.6-.5-.6-1.4-.2-1.4.6 0 1.4-.6 2.6-1.7 3.2.3-3.3-1.1-6.3-3.6-8.6-.5-.4-1.3 0-1.2.7.3 2.7-.9 4.3-2.3 6C4.3 10.9 4.6 12.8 4.6 15c0 4.1 3.1 7 7.4 7z" /></I>,
  drop: () => <I><path d="M12 2.6c-.3 0-.6.2-.8.4C9 6 5.5 10.6 5.5 14.6A6.5 6.5 0 0012 21.4a6.5 6.5 0 006.5-6.8c0-4-3.5-8.6-5.7-11.6-.2-.2-.5-.4-.8-.4z" /></I>,
  leaf: () => <I><path d="M20.5 3.5c-8.6-.3-14.6 2.6-15.9 9.3-.4 2-.1 3.8.8 5.3L3.5 20l1.2 1.2 1.9-1.9c1.5 1 3.3 1.3 5.3.9 6.7-1.3 9.6-7.3 9.3-15.9a.8.8 0 00-.7-.8zM8 17.2c1.6-3.4 4.2-6 7.6-7.6l.6 1.2c-3.1 1.5-5.4 3.8-6.9 6.9z" /></I>,
  layers: () => <I><path d="M12 3l9.5 5L12 13 2.5 8zM4.6 11.3L12 15.2l7.4-3.9 2.1 1.1L12 17.4l-9.5-5zm0 4L12 19.2l7.4-3.9 2.1 1.1L12 21.4l-9.5-5z" /></I>,
  people: () => <I><circle cx="9" cy="7.5" r="3.6" /><path d="M2.5 19.2c.4-3.9 3.1-6.2 6.5-6.2s6.1 2.3 6.5 6.2c0 .5-.4.8-.9.8H3.4c-.5 0-.9-.3-.9-.8z" /><circle cx="17.2" cy="8.6" r="2.8" opacity=".6" /><path d="M16.3 13.4c3 .1 5 2 5.2 5 0 .4-.3.6-.7.6h-3.6c0-2.1-.3-4-.9-5.6z" opacity=".6" /></I>,
  pin: () => <I><path d="M12 2.2c-4.1 0-7.3 3.1-7.3 7.2 0 5.2 6 11.2 6.7 11.9.3.3.9.3 1.2 0 .7-.7 6.7-6.7 6.7-11.9 0-4.1-3.2-7.2-7.3-7.2zm0 9.9a2.7 2.7 0 110-5.4 2.7 2.7 0 010 5.4z" /></I>,
  shield: () => <I><path d="M12 2.3l7.6 2.9c.5.2.8.6.8 1.1v5.2c0 5-3.4 9-8 10.3-.3.1-.5.1-.8 0-4.6-1.3-8-5.3-8-10.3V6.3c0-.5.3-.9.8-1.1z" /></I>,
  alert: () => <I><path d="M10.6 3.3c.6-1.1 2.2-1.1 2.8 0l8.3 14.9c.6 1.1-.2 2.4-1.4 2.4H3.7c-1.2 0-2-1.3-1.4-2.4zM11 9v5h2V9zm0 6.6v2h2v-2z" /></I>,
  check: () => <I><path d="M12 2a10 10 0 110 20 10 10 0 010-20zm4.3 6.3l-5.6 5.6-2.9-2.9-1.4 1.4 4.3 4.3 7-7z" /></I>,
  pencil: () => <I><path d="M15.6 3.6a2 2 0 012.8 0l2 2a2 2 0 010 2.8L9 19.8l-5.3 1.5c-.6.2-1.1-.4-1-1L4.2 15z" /></I>,
};

export function Chev() {
  return (
    <svg viewBox="0 0 8 14" className="h-3 w-2 text-muted/70" aria-hidden>
      <path d="M1.5 1.5L6.5 7l-5 5.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
