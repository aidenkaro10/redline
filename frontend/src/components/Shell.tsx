"use client";

/**
 * Page frame: the man-down / SOS screen sits on top of every page.
 * Each app draws its own header (phone apps: AppHeader; demo pages: Nav).
 */

import { usePathname } from "next/navigation";

import { ManDownOverlay } from "./ManDownOverlay";

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const marketing = path.startsWith("/preview");     // the selling page isn't an app screen
  return (
    <>
      {children}
      {!marketing && <ManDownOverlay />}
    </>
  );
}
