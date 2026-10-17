"use client";

/**
 * Crew photo report: a worker snaps a photo with a note (snake, standing water,
 * downed line...). It shows up on the crew lead's map.
 * The photo is shrunk in the browser before sending, so it uploads fast on a phone.
 */

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Card, fmtClock } from "@/components/ui";
import type { PhotoReport, ReportKind, Site } from "@/lib/contract";
import { useCrew } from "@/lib/crew";
import { apiPost, useFetch, useLive } from "@/lib/live";

const KINDS: { v: ReportKind; t: string }[] = [
  { v: "snake", t: "Snake" },
  { v: "alligator", t: "Alligator" },
  { v: "fire_ants", t: "Fire ants" },
  { v: "standing_water", t: "Standing water" },
  { v: "downed_line", t: "Downed line" },
  { v: "other", t: "Other" },
];

export default function ReportPage() {
  return <Suspense><ReportForm /></Suspense>;
}

function ReportForm() {
  const params = useSearchParams();
  const workerId = params.get("worker");
  const { data: sites } = useFetch<Site[]>("/sites");
  const { reports, workers } = useLive();
  const crew = useCrew();
  const [picked, setSiteId] = useState<string | null>(null);
  // Default: the reporting worker's site, else the crew's first site.
  const siteId = picked ?? (workerId && workers[workerId] ? workers[workerId].site_id : crew.siteIds[0] ?? "");
  const [kind, setKind] = useState<ReportKind>("snake");
  const [note, setNote] = useState("");
  const [photo, setPhoto] = useState<string | null>(null);
  const [coords, setCoords] = useState<{ lat: number; lon: number } | null>(null);
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");

  const onPhoto = async (f: File | undefined) => setPhoto(f ? await shrink(f) : null);

  const useMyLocation = () => navigator.geolocation?.getCurrentPosition(
    (p) => setCoords({ lat: p.coords.latitude, lon: p.coords.longitude }),
    () => setCoords(null),
  );

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setState("sending");
    try {
      const r = await apiPost<{ ok: boolean }>("/reports", { site_id: siteId, worker_id: workerId, kind, note, photo, ...(coords ?? {}) });
      setState(r.ok ? "sent" : "error");
      if (r.ok) { setNote(""); setPhoto(null); }
    } catch { setState("error"); }
  };

  const crewSites = (sites ?? []).filter((s) => crew.siteIds.includes(s.id));
  const otherSites = (sites ?? []).filter((s) => !crew.siteIds.includes(s.id));

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-4 px-4 py-6">
      <div>
        <p className="text-xs font-bold uppercase tracking-wider text-accent">Crew photo report</p>
        <h1 className="text-2xl font-bold tracking-tight">Report a hazard</h1>
        <p className="text-sm text-ink-2">Snap a photo, add a note. It shows on the crew lead&apos;s map right away.</p>
      </div>

      <Card>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm font-semibold">
            Site
            <select value={siteId} onChange={(e) => setSiteId(e.target.value)} className="rounded-lg border border-line bg-surface px-3 py-2 font-normal">
              <optgroup label="Your crew">{crewSites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</optgroup>
              <optgroup label="Other sites">{otherSites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</optgroup>
            </select>
          </label>

          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1 text-sm font-semibold">What did you see?</legend>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {KINDS.map((k) => (
                <button type="button" key={k.v} onClick={() => setKind(k.v)}
                  className={`rounded-lg border px-3 py-2.5 text-sm font-semibold ${kind === k.v ? "border-accent bg-accent text-accent-ink" : "border-line hover:bg-surface-2"}`}>
                  {k.t}
                </button>
              ))}
            </div>
          </fieldset>

          <label className="flex flex-col gap-1 text-sm font-semibold">
            Note
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={500} required
              placeholder="Where is it? e.g. Cottonmouth by the north fence, near the transformer pad."
              className="rounded-lg border border-line bg-surface px-3 py-2 font-normal" />
          </label>

          <div className="flex flex-col gap-2 text-sm font-semibold">
            Photo
            <input type="file" accept="image/*" capture="environment" onChange={(e) => onPhoto(e.target.files?.[0])}
              className="text-sm font-normal file:mr-3 file:rounded-lg file:border-0 file:bg-surface-2 file:px-3 file:py-2 file:font-semibold file:text-ink" />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {photo && <img src={photo} alt="Your photo" className="max-h-56 w-fit rounded-lg border border-line" />}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-sm">
            <button type="button" onClick={useMyLocation} className="rounded-lg border border-line px-3 py-2 font-semibold hover:bg-surface-2">Use my location</button>
            <span className="text-ink-2">{coords ? `${coords.lat.toFixed(4)}, ${coords.lon.toFixed(4)}` : "Defaults to the site location"}</span>
          </div>

          <div className="flex items-center gap-3">
            <button type="submit" disabled={state === "sending"} className="rounded-lg bg-accent px-5 py-2.5 font-bold text-accent-ink disabled:opacity-60">
              {state === "sending" ? "Sending…" : "Send report"}
            </button>
            {state === "sent" && <span className="text-sm font-semibold text-ok-ink">✓ Sent. It&apos;s on the map.</span>}
            {state === "error" && <span className="text-sm font-semibold text-danger-ink">Couldn&apos;t send. Try again.</span>}
          </div>
        </form>
      </Card>

      <Card title="Recent reports">
        <ul className="space-y-3">
          {[...reports].reverse().map((r: PhotoReport) => (
            <li key={r.id} className="flex gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {r.photo ? <img src={r.photo} alt="" className="h-14 w-14 rounded-lg object-cover" /> : <div className="grid h-14 w-14 place-items-center rounded-lg bg-surface-2 text-[10px] text-muted">no photo</div>}
              <div className="min-w-0">
                <p className="text-sm font-semibold capitalize">{r.kind.replace("_", " ")} <span className="font-normal text-muted">· {sites?.find((s) => s.id === r.site_id)?.name} · {fmtClock(r.at)}</span></p>
                <p className="text-sm text-ink-2">{r.note}</p>
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </main>
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
