"use client";

/**
 * The site map (Leaflet). Loaded only in the browser, see SiteMap.tsx.
 *
 * - Each site is a dot colored by tomorrow's 0-10 hazard index (light = low, dark red = high).
 * - A red ring means a worker on that site has a heat alert or a man-down.
 * - Lightning: dashed 10-mile ring around the site plus the recent strikes.
 * - Crew photo reports: small camera pins.
 * - Wildlife sightings (iNaturalist): an amber pin at the spot it was seen, with a line to the
 *   site. Blurred (approximate) sightings get a dashed outline and say so.
 * - Crew members who share their phone's location: a round pin with their initials, colored by
 *   status. The first time people appear, the map zooms to them.
 */

import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { useEffect, useRef } from "react";

import type { PhotoReport, Site, SiteStatus } from "@/lib/contract";
import { lightningRings } from "@/lib/map-layers";
import { indexColor } from "./ui";

const FLORIDA: L.LatLngBoundsExpression = [[24.5, -87.6], [31.0, -80.0]];

// OpenStreetMap's free tiles (attribution required, no key). Dark mode darkens
// them with a CSS filter (see globals.css), so there's only one tile source.
// Needs internet. Without it the dots still show on a plain background.
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

export interface MapPerson {
  id: string;
  name: string;
  status: "green" | "yellow" | "red";
  lat: number;
  lon: number;
  minutesAgo: number;
}

const PERSON_COLOR = { green: "#34c759", yellow: "#ff9500", red: "#ff3b30" } as const;

export interface SiteMapProps {
  people?: MapPerson[];
  sites: Site[];
  statuses: Record<string, SiteStatus>;
  reports: PhotoReport[];
  selected: string | null;
  onSelect: (id: string) => void;
  dark: boolean;
  focus?: string | null;          // site to zoom to (changes trigger a fly-to)
}

export default function SiteMapInner({ sites, statuses, reports, selected, onSelect, dark, focus, people = [] }: SiteMapProps) {
  const fittedPeople = useRef(false);
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const moved = useRef(false);   // true once a person or a fly-to moved the map
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  // Create the map once.
  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, scrollWheelZoom: false });
    m.fitBounds(FLORIDA);
    L.tileLayer(TILE_URL, {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 18,
    }).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    // Keep the map sized right when its box changes. Until someone touches
    // the map, keep re-fitting Florida so the first view is always the whole state.
    const touch = () => { moved.current = true; };
    el.current.addEventListener("pointerdown", touch);
    el.current.addEventListener("wheel", touch);
    const ro = new ResizeObserver(() => {
      m.invalidateSize();
      if (!moved.current) m.fitBounds(FLORIDA);
    });
    ro.observe(el.current);
    // A new map starts un-zoomed (React can mount this twice in development)
    return () => { ro.disconnect(); m.remove(); map.current = null; fittedPeople.current = false; moved.current = false; };
  }, []);

  // Zoom to a site when asked.
  useEffect(() => {
    const s = sites.find((x) => x.id === focus);
    if (s && map.current) {
      moved.current = true;
      map.current.flyTo([s.lat, s.lon], 9, { duration: 0.8 });
    }
  }, [focus, sites]);

  // Redraw markers whenever the data changes.
  useEffect(() => {
    const g = layer.current;
    if (!g) return;
    g.clearLayers();
    const ink = dark ? "#f0efec" : "#0b0b0b";

    for (const s of sites) {
      const st = statuses[s.id];
      if (!st) continue;
      const lightning = st.hazards.find((h) => h.kind === "lightning");
      const rings = lightningRings(lightning);
      for (const r of rings) {
        L.circle([s.lat, s.lon], r.kind === "stop"
          ? { radius: r.radius_m, color: "#9085e9", weight: 2, dashArray: "6 6", fillColor: "#6250d6", fillOpacity: 0.08, interactive: false }
          : { radius: r.radius_m, color: "#9085e9", weight: 1, dashArray: "2 6", fill: false, interactive: false }).addTo(g);
      }
      if (lightning?.stop_work) {
        for (const k of lightning.lightning?.strikes ?? []) {
          L.marker([k.lat, k.lon], {
            interactive: false,
            icon: L.divIcon({ className: "", iconSize: [18, 18], html: `<svg viewBox="0 0 24 24" width="18" height="18"><path d="M13 2L5 13h6l-1 9 8-11h-6z" fill="#fab219" stroke="#6250d6" stroke-width="1.5" stroke-linejoin="round"/></svg>` }),
          }).addTo(g);
        }
      }
    }

    // Draw high-index sites last so they sit on top.
    const ordered = [...sites].sort((a, b) => (statuses[a.id]?.index_tomorrow ?? 0) - (statuses[b.id]?.index_tomorrow ?? 0));
    for (const s of ordered) {
      const st = statuses[s.id];
      if (!st) continue;
      const flagged = st.worker_flag !== "none";
      const isSel = selected === s.id;
      if (flagged) {
        L.marker([s.lat, s.lon], {
          interactive: false,
          icon: L.divIcon({
            className: "", iconSize: [34, 34],
            html: `<span class="ring-pulse" style="position:absolute;inset:0;border-radius:9999px;color:${st.worker_flag === "mandown" ? "#d03b3b" : st.worker_flag === "sos" ? "#7e3ac8" : "#ec835a"}"></span>`,
          }),
        }).addTo(g);
      }
      const dot = L.circleMarker([s.lat, s.lon], {
        radius: isSel ? 10 : st.workers_on_site ? 8 : 6,
        color: isSel ? ink : flagged ? "#d03b3b" : dark ? "#1a1a19" : "#fcfcfb",
        weight: isSel || flagged ? 3 : 1.5,
        fillColor: indexColor(st.index_tomorrow),
        fillOpacity: 1,
      }).addTo(g);
      const stop = st.hazards.some((h) => h.stop_work);
      dot.bindTooltip(
        `<b>${s.name}</b><br/>Tomorrow's index: <b>${st.index_tomorrow === null ? "--" : st.index_tomorrow.toFixed(1)}</b>/10${st.hazard_index ? ` (${st.hazard_index.band})` : ""}` +
        (st.hazard_index?.top_reasons[0] ? `<br/><span style="opacity:.8">${escapeHtml(st.hazard_index.top_reasons[0].text)}</span>` : "") +
        (st.workers_on_site ? `<br/>${st.workers_on_site} crew on site` : "") +
        (flagged ? `<br/><b style="color:${st.worker_flag === "sos" ? "#7e3ac8" : "#d03b3b"}">${st.worker_flag === "mandown" ? "MAN DOWN" : st.worker_flag === "sos" ? "SOS" : "Worker heat alert"}</b>` : "") +
        (stop ? `<br/><b>Stop work: lightning</b>` : ""),
        { direction: "top", offset: [0, -6] },
      );
      dot.on("click", () => onSelectRef.current(s.id));
    }

    for (const r of reports) {
      L.marker([r.lat, r.lon], {
        icon: L.divIcon({
          className: "", iconSize: [22, 22], iconAnchor: [11, 11],
          html: `<div style="width:22px;height:22px;border-radius:6px;background:#1baf7a;border:2px solid ${dark ? "#1a1a19" : "#fff"};display:grid;place-items:center"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#fff" stroke-width="2"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg></div>`,
        }),
      }).bindTooltip(`<b>Crew report: ${r.kind.replace("_", " ")}</b><br/>${escapeHtml(r.note)}`, { direction: "top" }).addTo(g);
    }

    // Wildlife sightings, at the point they were actually seen
    for (const s of sites) {
      const seen = statuses[s.id]?.hazards.find((h) => h.kind === "wildlife")?.sighting;
      if (!seen) continue;
      L.polyline([[s.lat, s.lon], [seen.lat, seen.lon]], { color: "#ff9500", weight: 1.5, opacity: 0.7, dashArray: "4 4", interactive: false }).addTo(g);
      L.marker([seen.lat, seen.lon], {
        icon: L.divIcon({
          className: "", iconSize: [24, 24], iconAnchor: [12, 12],
          html: `<div style="width:24px;height:24px;border-radius:50%;background:#ff9500;border:2px ${seen.approximate ? "dashed" : "solid"} ${dark ? "#1a1a19" : "#fff"};display:grid;place-items:center;box-shadow:0 1px 4px rgba(0,0,0,.3)"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"><path d="M4 17c3 0 3-4 6-4s3 4 6 4 3-3 4-5"/><circle cx="19.5" cy="10.5" r="1.5"/></svg></div>`,
        }),
      }).bindTooltip(
        `<b>${escapeHtml(seen.common)}</b><br/>${seen.approximate ? "Near" : `${seen.miles} mi from`} ${escapeHtml(s.name)}` +
        `<br/>Seen ${escapeHtml(seen.observed_on)} · iNaturalist${seen.approximate ? "<br/><i>Approximate spot (iNaturalist hides the exact one)</i>" : ""}`,
        { direction: "top", offset: [0, -8] },
      ).addTo(g);
    }
    // Crew members sharing their location
    for (const p of people) {
      const initials = p.name.split(" ").map((x) => x[0]).slice(0, 2).join("").toUpperCase();
      L.marker([p.lat, p.lon], {
        zIndexOffset: 1000,
        icon: L.divIcon({
          className: "", iconSize: [30, 30], iconAnchor: [15, 15],
          html: `<div style="width:30px;height:30px;border-radius:50%;background:${PERSON_COLOR[p.status]};border:2.5px solid ${dark ? "#1a1a19" : "#fff"};display:grid;place-items:center;color:#fff;font:700 11px/1 system-ui,sans-serif;box-shadow:0 2px 6px rgba(0,0,0,.35)">${escapeHtml(initials)}</div>`,
        }),
      }).bindTooltip(`<b>${escapeHtml(p.name)}</b><br/>${p.minutesAgo < 1 ? "Just now" : `${p.minutesAgo} min ago`}`, { direction: "top", offset: [0, -14] }).addTo(g);
    }
  }, [sites, statuses, reports, selected, dark, people]);

  // The first time anyone shares their location, zoom to the crew.
  useEffect(() => {
    if (!map.current || fittedPeople.current || people.length === 0) return;
    fittedPeople.current = true;
    moved.current = true;
    map.current.fitBounds(L.latLngBounds(people.map((p) => [p.lat, p.lon] as [number, number])).pad(0.4), { maxZoom: 15 });
  }, [people]);

  return <div ref={el} className="h-full w-full" />;
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
