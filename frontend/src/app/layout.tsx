import type { Metadata, Viewport } from "next";
import { Mona_Sans } from "next/font/google";
import "./globals.css";

import { LiveProvider } from "@/lib/live";
import { Shell } from "@/components/Shell";

// The phone apps' typeface (GitHub's Mona Sans). Bundled with the app so it looks the
// same on iPhone, Android and laptops. Pages opt in with the "font-app" class.
const mona = Mona_Sans({ subsets: ["latin"], variable: "--font-mona", display: "swap" });

export const metadata: Metadata = {
  title: "PlanIT Control",
  description: "Heat strain forecasting for outdoor crews",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f4f1" },
    { media: "(prefers-color-scheme: dark)", color: "#0b0b0c" },
  ],
};

// Runs before the page paints, so dark mode doesn't flash white.
const THEME_SCRIPT = `try{var t=localStorage.getItem("theme");var d=t?t==="dark":matchMedia("(prefers-color-scheme: dark)").matches;document.documentElement.classList.toggle("dark",d)}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`h-full antialiased ${mona.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-full">
        <LiveProvider>
          <Shell>{children}</Shell>
        </LiveProvider>
      </body>
    </html>
  );
}
