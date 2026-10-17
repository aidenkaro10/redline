import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
