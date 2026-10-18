import type { NextConfig } from "next";
import { networkInterfaces } from "node:os";

// This Mac's own Wi-Fi / LAN addresses (e.g. 10.136.x.x), so a phone on the same
// network can open the dev server. Next blocks other addresses in dev by default.
const lanAddresses = Object.values(networkInterfaces())
  .flat()
  .filter((n) => n && n.family === "IPv4" && !n.internal)
  .map((n) => n!.address);

const nextConfig: NextConfig = {
  // NEXT_DIST_DIR lets a second dev server (e.g. pointed at the real backend) run
  // next to the mock one without the two fighting over the same .next folder.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  allowedDevOrigins: lanAddresses,
  // Tailwind CSS is compiled by Turbopack (the dev server's bundler).
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
