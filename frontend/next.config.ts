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
  // On the hosted site (Render sets RENDER=true), the bare address opens the selling page.
  // Locally "/" stays the phone crew app, so tests and other sessions are unaffected.
  async redirects() {
    return process.env.RENDER ? [{ source: "/", destination: "/preview", permanent: false }] : [];
  },
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
