/**
 * The Redline logo: a dark tile with a red heartbeat line spiking up to a white dot that
 * sits on a dashed "red line", next to the wordmark RED (red) + LINE (the page's text color).
 * Drawn as SVG so it stays sharp at any size, on light and dark pages.
 */

const RED = "#ff2d1a";

/** Just the tile (also used as the browser tab icon, see app/icon.svg). */
export function RedlineMark({ className = "h-8 w-8", px }: { className?: string; px?: number }) {
  return (
    <svg viewBox="0 0 28 28" className={className} width={px} height={px} style={px ? { width: px, height: px } : undefined} aria-hidden>
      <rect width="28" height="28" rx="6.5" fill="#1b1b1d" />
      <path d="M4 6.6H24" stroke="#9b2016" strokeWidth="1.1" strokeDasharray="1.7 1.3" />
      <path d="M3.6 16H8.3L9.8 13.2L11.6 18.8L14 6.9L16.1 19.9L18 15L19.3 16H24.4" fill="none" stroke={RED} strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="14" cy="6.6" r="2.15" fill="#f4f4f4" />
    </svg>
  );
}

/** Tile + wordmark. `size` is the wordmark height in px (the tile scales with it). */
export function RedlineLogo({ size = 17, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`} aria-label="Redline">
      <RedlineMark className="shrink-0" px={Math.round(size * 1.75)} />
      <span aria-hidden className="leading-none" style={{
        fontFamily: 'var(--font-mona), "Helvetica Neue", Arial, sans-serif', fontWeight: 900, fontSize: size, letterSpacing: "-0.01em",
      }}>
        <span style={{ color: RED }}>RED</span><span>LINE</span>
      </span>
    </span>
  );
}
