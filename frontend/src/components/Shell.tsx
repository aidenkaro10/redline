"use client";

/**
 * Page frame: the top bar (hidden on the worker phone view) and the
 * man-down screen, which sits on top of every page.
 */

import { usePathname } from "next/navigation";

import { Nav } from "./Nav";
import { ManDownOverlay } from "./ManDownOverlay";

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const phone = path.startsWith("/worker/");
  return (
    <>
      {!phone && <Nav />}
      {children}
      <ManDownOverlay />
    </>
  );
}
