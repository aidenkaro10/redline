"use client";

/**
 * Report a hazard from the worker's phone: what it is, a note, an optional photo.
 * It goes to the worker's own site and shows on the crew lead's map.
 * The photo is shrunk in the browser first, so it uploads fast on a phone.
 */

import { useState } from "react";

import type { ReportKind } from "@/lib/contract";
import { apiPost } from "@/lib/live";

const KINDS: { v: ReportKind; t: string }[] = [
  { v: "snake", t: "Snake" },
  { v: "alligator", t: "Gator" },
  { v: "fire_ants", t: "Fire ants" },
  { v: "standing_water", t: "Water" },
  { v: "downed_line", t: "Downed line" },
  { v: "other", t: "Other" },
];

export function ReportForm({ workerId, siteId }: { workerId: string; siteId: string }) {
  const [kind, setKind] = useState<ReportKind>("snake");
  const [note, setNote] = useState("");
  const [photo, setPhoto] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setState("sending");
    // Use the phone's location if it allows it quickly; otherwise the site's location.
    const coords = await new Promise<{ lat: number; lon: number } | null>((resolve) => {
      if (!navigator.geolocation) return resolve(null);
      navigator.geolocation.getCurrentPosition(
        (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }),
        () => resolve(null),
        { timeout: 3000 },
      );
    });
    try {
      const r = await apiPost<{ ok: boolean }>("/reports", { site_id: siteId, worker_id: workerId, kind, note, photo, ...(coords ?? {}) });
      setState(r.ok ? "sent" : "error");
      if (r.ok) { setNote(""); setPhoto(null); }
    } catch { setState("error"); }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-2.5">
      <div className="grid grid-cols-3 gap-1.5">
        {KINDS.map((k) => (
          <button type="button" key={k.v} onClick={() => setKind(k.v)}
            className={`min-h-11 rounded-xl px-2 py-2 text-sm font-semibold ${kind === k.v ? "bg-accent text-accent-ink" : "bg-surface-2 text-ink"}`} aria-pressed={kind === k.v}>
            {k.t}
          </button>
        ))}
      </div>
      <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} required placeholder="Where? e.g. by the north fence"
        className="min-h-11 rounded-xl border border-line bg-page px-3 py-2 text-base placeholder:text-muted" />
      <label className="flex items-center gap-2 text-sm">
        <span className="rounded-xl bg-surface-2 px-3 py-2 font-semibold text-accent">{photo ? "Photo added ✓" : "Add photo"}</span>
        <input type="file" accept="image/*" capture="environment" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; setPhoto(f ? await shrink(f) : null); }} />
      </label>
      <button disabled={state === "sending"} className="min-h-11 rounded-full bg-accent px-4 py-2.5 font-semibold text-accent-ink disabled:opacity-60">
        {state === "sending" ? "Sending…" : "Send report"}
      </button>
      {state === "sent" && <p className="text-sm font-semibold text-ok-ink">✓ Sent. Your crew lead can see it on the map.</p>}
      {state === "error" && <p className="text-sm font-semibold text-danger-ink">Couldn&apos;t send. Try again.</p>}
    </form>
  );
}

/** Resize a photo to at most 1024 px and turn it into a JPEG data URL. */
function shrink(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, 1024 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(img.src);
      resolve(c.toDataURL("image/jpeg", 0.8));
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}
