"use client";

/**
 * The live connection and the shared app state.
 *
 * - If NEXT_PUBLIC_BACKEND_URL is set (e.g. http://localhost:8000), we talk to
 *   the real FastAPI backend: REST at <url>/api/..., live events over a
 *   WebSocket at <url>/ws/live.
 * - If it's empty, we use the mock backend built into this Next.js app:
 *   REST at /api/..., live events over Server-Sent Events at /api/stream.
 *
 * Either way the events are the same LiveEvent shapes from contract.ts.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";

import type {
  Alert, BandCommand, DemoState, EmergencyCall, Estimate, LiveEvent, LogEntry, ManDown, PhotoReport,
  SiteStatus, ViewFilter, Vitals, WorkerStatus, OffsetUpdate,
} from "./contract";
import { canSee } from "./contract";
import { normalizeSite } from "./normalize";
import { toOffsetUpdate } from "./offset";
import { foldEmergencyStatus } from "./emergency.ts";

const BACKEND = (process.env.NEXT_PUBLIC_BACKEND_URL ?? "").replace(/\/$/, "");
export const USING_MOCK = BACKEND === "";

export function apiUrl(path: string) {
  return `${BACKEND}/api${path}`;
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(apiUrl(path), { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export async function apiPost<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(apiUrl(path), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export interface LiveState {
  connected: boolean;
  ready: boolean;                       // first snapshot arrived
  clock: { sim: number; wall: number; speed: number };
  workers: Record<string, WorkerStatus>;
  sites: Record<string, SiteStatus>;
  alerts: Alert[];
  mandown: ManDown | null;
  emergency: EmergencyCall | null;
  demo: DemoState | null;
  reports: PhotoReport[];
  log: LogEntry[];
  history: Record<string, { vitals: Vitals[]; estimates: Estimate[] }>;
  band: BandCommand[];
  offsets: Record<string, OffsetUpdate>;
}

const EMPTY: LiveState = {
  connected: false, ready: false, clock: { sim: 0, wall: 0, speed: 1 }, workers: {}, sites: {}, alerts: [],
  mandown: null, emergency: null, demo: null, reports: [], log: [], history: {}, band: [], offsets: {},
};

const MAX_VITALS = 240;      // 20 min at 5 s
const MAX_ESTIMATES = 90;    // 90 min

/** Apply a batch of events to the state. Returns a new state object. */
function applyEvents(prev: LiveState, events: LiveEvent[]): LiveState {
  const s: LiveState = { ...prev };
  const now = Date.now() / 1000;
  for (const ev of events) {
    switch (ev.type) {
      case "snapshot":
        Object.assign(s, {
          ready: true,
          clock: { sim: ev.sim_time, wall: now, speed: ev.demo.speed },
          workers: Object.fromEntries(ev.workers.map((w) => [w.worker_id, w])),
          sites: Object.fromEntries(ev.sites.map((x) => [x.site_id, normalizeSite(x)])),
          alerts: ev.alerts, mandown: ev.mandown, emergency: ev.emergency, demo: ev.demo,
          reports: ev.reports, log: ev.log, history: ev.history, band: ev.band, offsets: Object.fromEntries(Object.entries(ev.offsets ?? {})
            .map(([id, o]) => [id, toOffsetUpdate(o)] as const).filter((x): x is readonly [string, OffsetUpdate] => x[1] !== null)),
        });
        break;
      case "clock":
        s.clock = { sim: ev.sim_time, wall: now, speed: ev.speed };
        break;
      case "worker_status":
        s.workers = { ...s.workers, [ev.worker.worker_id]: ev.worker };
        break;
      case "site_status":
        s.sites = { ...s.sites, [ev.site.site_id]: normalizeSite(ev.site) };
        break;
      case "vitals": {
        const id = ev.vitals.worker_id;
        const h = s.history[id] ?? { vitals: [], estimates: [] };
        s.history = { ...s.history, [id]: { ...h, vitals: [...h.vitals, ev.vitals].slice(-MAX_VITALS) } };
        break;
      }
      case "estimate": {
        const id = ev.estimate.worker_id;
        const h = s.history[id] ?? { vitals: [], estimates: [] };
        s.history = { ...s.history, [id]: { ...h, estimates: [...h.estimates, ev.estimate].slice(-MAX_ESTIMATES) } };
        break;
      }
      case "alert": {
        const i = s.alerts.findIndex((a) => a.id === ev.alert.id);
        s.alerts = i >= 0 ? s.alerts.map((a, j) => (j === i ? ev.alert : a)) : [...s.alerts, ev.alert].slice(-100);
        break;
      }
      case "mandown":
        s.mandown = ev.mandown;
        break;
      case "emergency":
        s.emergency = ev.emergency;
        break;
      case "emergency_status":
        // Session D's small step-by-step events: fold each one into the same
        // EmergencyCall the man-down screen already reads (see lib/emergency.ts).
        s.emergency = foldEmergencyStatus(s.emergency, ev, s.mandown);
        break;
      case "demo":
        s.demo = ev.demo;
        s.clock = { ...s.clock, speed: ev.demo.speed };
        break;
      case "band_command":
        s.band = [...s.band, ev.band].slice(-30);
        break;
      case "report":
        s.reports = [...s.reports, ev.report];
        break;
      case "log":
        s.log = [...s.log, ev.entry].slice(-120);
        break;
      case "offset_update": {
        // Accepts this contract's shape and session A's flat one (lib/offset.ts).
        const flatId = (ev as unknown as { worker_id?: string }).worker_id;
        const id = ev.offset?.worker_id ?? flatId;
        const u = id ? toOffsetUpdate(ev, s.offsets[id]) : null;
        if (u) s.offsets = { ...s.offsets, [u.worker_id]: u };
        break;
      }
    }
  }
  return s;
}

// ---------------------------------------------------------------------------
// Connection (mock = Server-Sent Events, real = WebSocket)
// ---------------------------------------------------------------------------

/** If nothing arrives for this long, the connection is treated as dead and reopened. */
const SILENCE_MS = 8000;

function connect(filter: ViewFilter, onEvent: (ev: LiveEvent) => void, onStatus: (up: boolean) => void) {
  const qs = new URLSearchParams({ view: filter.view, ...(filter.worker_id ? { worker_id: filter.worker_id } : {}) });
  let closed = false;
  let lastMsg = Date.now();
  let reopen: () => void = () => {};

  // One bad message must never kill the whole page.
  const handle = (data: string) => {
    lastMsg = Date.now();
    try { onEvent(JSON.parse(data)); } catch (e) { console.warn("live: skipped a bad message", e); }
  };

  // Watchdog: both backends send a "clock" event twice a second, so silence means trouble.
  const watchdog = setInterval(() => {
    if (!closed && Date.now() - lastMsg > SILENCE_MS) {
      lastMsg = Date.now();
      onStatus(false);
      reopen();
    }
  }, 2000);

  if (USING_MOCK) {
    let es: EventSource | null = null;
    reopen = () => {
      es?.close();
      es = new EventSource(`/api/stream?${qs}`);
      es.onopen = () => onStatus(true);
      es.onerror = () => onStatus(false);       // EventSource reconnects by itself
      es.onmessage = (m) => handle(m.data);
    };
    reopen();
    return () => { closed = true; clearInterval(watchdog); es?.close(); };
  }

  // Real backend: WebSocket with a simple reconnect (1 s, 2 s, 4 s ... up to 10 s).
  let ws: WebSocket | null = null;
  let delay = 1000;
  const open = () => {
    if (closed) return;
    const sock = new WebSocket(`${BACKEND.replace(/^http/, "ws")}/ws/live?${qs}`);
    ws = sock;
    sock.onopen = () => { delay = 1000; onStatus(true); };
    sock.onmessage = (m) => handle(m.data);
    sock.onclose = () => {
      if (ws !== sock) return;                 // an old socket we already replaced
      onStatus(false);
      if (!closed) setTimeout(open, delay);
      delay = Math.min(delay * 2, 10000);
    };
  };
  reopen = () => { const old = ws; ws = null; old?.close(); open(); };
  open();
  return () => { closed = true; clearInterval(watchdog); ws?.close(); };
}

/** Which view this page is, from its URL. Decides what the server sends us. */
function filterForPath(path: string): ViewFilter {
  if (path.startsWith("/demo")) return { view: "demo" };
  const m = path.match(/^\/worker\/([^/]+)/);
  if (m) return { view: "worker", worker_id: decodeURIComponent(m[1]) };
  return { view: "crew" };
}

// ---------------------------------------------------------------------------
// React glue
// ---------------------------------------------------------------------------

const LiveContext = createContext<LiveState>(EMPTY);

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const filter = useMemo(() => filterForPath(path), [path]);
  const key = `${filter.view}:${filter.worker_id ?? ""}`;
  const [state, setState] = useState<LiveState>(EMPTY);
  const queue = useRef<LiveEvent[]>([]);

  useEffect(() => {
    setState(EMPTY);
    queue.current = [];
    // Events can arrive many times a second. Apply them in batches, 4 times a second.
    const flush = setInterval(() => {
      if (!queue.current.length) return;
      const batch = queue.current;
      queue.current = [];
      setState((s) => applyEvents(s, batch));
    }, 250);
    const stop = connect(
      filter,
      (ev) => {
        // Belt and braces for privacy: drop anything this view may not see, even if a backend sends it.
        if (!canSee(filter, ev)) return;
        if (ev.type === "snapshot" && filter.view === "crew") { ev.history = {}; ev.offsets = {}; }
        if (ev.type === "snapshot") queue.current = [];   // a snapshot replaces everything before it
        queue.current.push(ev);
      },
      (up) => setState((s) => ({ ...s, connected: up })),
    );
    return () => { stop(); clearInterval(flush); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}

export function useLive() {
  return useContext(LiveContext);
}

/** The sim clock (replay time), smoothly ticking between server updates. */
export function useSimNow(periodMs = 250) {
  const { clock } = useLive();
  const calc = useCallback(() => (clock.sim ? clock.sim + (Date.now() / 1000 - clock.wall) * clock.speed : 0), [clock]);
  const [now, setNow] = useState(calc);
  useEffect(() => {
    setNow(calc());
    const t = setInterval(() => setNow(calc()), periodMs);
    return () => clearInterval(t);
  }, [calc, periodMs]);
  return now;
}

/** Fetch something once (and again every `refreshMs`, if given). */
export function useFetch<T>(path: string | null, refreshMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () => apiGet<T>(path).then((d) => { if (alive) { setData(d); setError(null); } }).catch((e) => alive && setError(String(e)));
    load();
    const t = refreshMs ? setInterval(load, refreshMs) : null;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [path, refreshMs]);
  return { data, error };
}
