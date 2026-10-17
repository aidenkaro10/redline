"use client";

/**
 * Bottom sheet, like Find My or Apple Maps: slides up over the page.
 * The phone apps keep the main screen simple and put "more" in here,
 * so the extra info only shows when someone asks for it.
 *
 * Closes on "Done", a tap on the dark backdrop, or the Escape key.
 */

import { useEffect } from "react";

export function Sheet({ open, onClose, title, children }: {
  open: boolean; onClose: () => void; title?: React.ReactNode; children: React.ReactNode;
}) {
  // Escape closes it (keyboard users, and laptops during the demo).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[900] flex items-end justify-center" role="dialog" aria-modal="true">
      {/* Backdrop: tap anywhere outside the sheet to close */}
      <button aria-label="Close" onClick={onClose} className="sheet-fade absolute inset-0 cursor-default bg-black/35" />
      <div className="sheet-up relative flex max-h-[90vh] w-full max-w-xl flex-col rounded-t-[22px] bg-page text-ink shadow-[0_-8px_40px_rgba(0,0,0,0.18)]">
        {/* Grab handle + header */}
        <div className="shrink-0 px-4 pt-2">
          <div className="mx-auto h-1.5 w-10 rounded-full bg-muted/40" aria-hidden />
          <div className="flex min-h-11 items-center justify-between gap-3 pt-1">
            <h2 className="min-w-0 truncate text-[17px] font-semibold">{title}</h2>
            <button onClick={onClose} className="min-h-11 px-1 text-[17px] font-semibold text-accent">Done</button>
          </div>
        </div>
        <div className="overflow-y-auto overscroll-contain px-4 pb-[max(24px,env(safe-area-inset-bottom))] pt-1">{children}</div>
      </div>
    </div>
  );
}

/** A white rounded group, like an iOS inset list section, with hairlines between rows. Optional small caps title above it. */
export function Group({ title, children, className = "" }: { title?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={className}>
      {title && <p className="mb-1.5 px-4 text-[13px] font-medium uppercase tracking-wide text-muted">{title}</p>}
      <div className="divide-y divide-line overflow-hidden rounded-2xl bg-surface">{children}</div>
    </section>
  );
}

/** One label / value row inside a Group. */
export function Row({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex min-h-11 items-center justify-between gap-3 px-4 py-2.5">
      <span className="text-[15px] text-ink-2">{label}</span>
      <span className="text-right text-[15px] font-semibold">{children}</span>
    </div>
  );
}

/** Chevron used at the end of tappable rows. */
export function Chevron({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
