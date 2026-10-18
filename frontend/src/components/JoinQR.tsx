"use client";

/**
 * "Add your crew": a QR code workers scan with their phone camera to open the worker app,
 * already filtered to this crew (/worker?team=...). The link is shown too, with a copy button,
 * for anyone who'd rather text it.
 * Drawn as an SVG with qrcode-generator (tiny, no dependencies).
 */

import qrcode from "qrcode-generator";
import { useEffect, useMemo, useState } from "react";

import { Icon } from "./health";

export function JoinQR({ team, teamName }: { team: string; teamName: string }) {
  const [origin, setOrigin] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => setOrigin(window.location.origin), []);
  const link = origin ? `${origin}/worker?team=${encodeURIComponent(team)}` : "";

  // One square per dark module, so the code stays sharp at any size and follows the text color
  const cells = useMemo(() => {
    if (!link) return null;
    const qr = qrcode(0, "M");
    qr.addData(link);
    qr.make();
    const n = qr.getModuleCount();
    let d = "";
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c} ${r}h1v1h-1z`;
    return { n, d };
  }, [link]);

  const copy = async () => {
    try { await navigator.clipboard.writeText(link); setCopied(true); setTimeout(() => setCopied(false), 1600); }
    catch { /* clipboard blocked: the link is still on screen to select */ }
  };

  return (
    <div className="rounded-[16px] bg-surface px-4 py-4 shadow-[0_1px_2px_rgba(20,20,10,0.04)]">
      <p className="flex items-center gap-2 text-[15px] font-medium">
        <span className="h-4 w-4 text-[#007aff]"><Icon.people /></span>
        <span className="text-ink-2">Add your crew</span>
      </p>
      <div className="mt-4 flex items-center gap-4">
        <div className="grid h-[132px] w-[132px] shrink-0 place-items-center rounded-xl bg-white p-2.5 text-[#121212]">
          {cells
            ? <svg viewBox={`-1 -1 ${cells.n + 2} ${cells.n + 2}`} className="h-full w-full" role="img" aria-label={`QR code to join ${teamName}`} shapeRendering="crispEdges"><path d={cells.d} fill="currentColor" /></svg>
            : <span className="h-full w-full animate-pulse rounded bg-black/5" />}
        </div>
        <div className="min-w-0">
          <p className="text-[17px] font-semibold leading-snug">Scan to join {teamName}</p>
          <p className="mt-1 text-[14px] leading-snug text-muted">Workers point their phone camera here, tap their name, and they&apos;re on.</p>
          <button onClick={copy} disabled={!link}
            className="mt-2 max-w-full truncate rounded-full bg-surface-2 px-3 py-1.5 text-left text-[13px] font-medium text-ink-2 transition-colors hover:bg-black/10 dark:hover:bg-white/15">
            {copied ? "Link copied" : link ? link.replace(/^https?:\/\//, "") : "…"}
          </button>
        </div>
      </div>
    </div>
  );
}
