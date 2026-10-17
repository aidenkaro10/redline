"use client";

/**
 * The site map (Leaflet). Loaded only in the browser, see SiteMap.tsx.
 *
 * - Each site is a dot colored by tomorrow's 0-10 hazard index (light = low, dark red = high).
 * - A red ring means a worker on that site has a heat alert or a man-down.
 * - Lightning: dashed 10-mile ring around the site plus the recent strikes.
 * - Crew photo reports: small camera pins.
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

export interface SiteMapProps {
  sites: Site[];
  statuses: Record<string, SiteStatus>;
  reports: PhotoReport[];
  selected: string | null;
  onSelect: (id: string) => void;
  dark: boolean;
  focus?: string | null;          // site to zoom to (changes trigger a fly-to)
}

export default function SiteMapInner({ sites, statuses, reports, selected, onSelect, dark, focus }: SiteMapProps) {
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
    return () => { ro.disconnect(); m.remove(); map.current = null; };
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
  }, [sites, statuses, reports, selected, dark]);

  return <div ref={el} className="h-full w-full" />;
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
