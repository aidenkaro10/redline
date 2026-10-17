"use client";

/**
 * Page frame: the man-down / SOS screen sits on top of every page.
 * Each app draws its own header (phone apps: AppHeader; demo pages: Nav).
 */

import { ManDownOverlay } from "./ManDownOverlay";

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      {children}
      <ManDownOverlay />
    </>
  );
}
