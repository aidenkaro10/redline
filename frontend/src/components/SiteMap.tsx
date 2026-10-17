"use client";

// Leaflet needs the browser's `window`, so the map is only loaded on the client.
import dynamic from "next/dynamic";

export const SiteMap = dynamic(() => import("./SiteMapInner"), {
  ssr: false,
  loading: () => <div className="grid h-full w-full place-items-center text-sm text-muted">Loading map…</div>,
});
