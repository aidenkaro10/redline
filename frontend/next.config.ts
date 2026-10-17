import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // NEXT_DIST_DIR lets a second dev server (e.g. pointed at the real backend) run
  // next to the mock one without the two fighting over the same .next folder.
  distDir: process.env.NEXT_DIST_DIR || ".next",
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
