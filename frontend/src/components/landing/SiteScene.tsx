"use client";

/**
 * The selling page's scene: a small isometric power plant (Deerhaven) with a
 * crew whose hard hats glow in their status color. Hover a worker to see their
 * forecast. Click the sky to roll in a storm (lightning, 10-mile ring, stop work).
 *
 * Pure SVG drawn by code: no images, no 3D library.
 */

import { useState } from "react";

// ---------------------------------------------------------------------------
// Isometric projection: ground x/y (meters-ish) and height z -> screen.
// ---------------------------------------------------------------------------
const S = 37;              // size of one ground unit on screen
const OX = 405, OY = 190;  // where ground (0, 0) lands
const C = 0.866, H = 0.5;  // cos 30°, sin 30°

function iso(x: number, y: number, z = 0): [number, number] {
  return [OX + (x - y) * S * C, OY + (x + y) * S * H - z * S];
}
const pts = (p: [number, number][]) => p.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");

interface BoxProps { x: number; y: number; z?: number; w: number; d: number; h: number; top: string; left: string; right: string }

/** A box seen from the front-right: top face plus the two faces facing the viewer. */
function Box({ x, y, z = 0, w, d, h, top, left, right }: BoxProps) {
  return (
    <g>
      <polygon points={pts([iso(x, y + d, z), iso(x + w, y + d, z), iso(x + w, y + d, z + h), iso(x, y + d, z + h)])} fill={left} />
      <polygon points={pts([iso(x + w, y, z), iso(x + w, y + d, z), iso(x + w, y + d, z + h), iso(x + w, y, z + h)])} fill={right} />
      <polygon points={pts([iso(x, y, z + h), iso(x + w, y, z + h), iso(x + w, y + d, z + h), iso(x, y + d, z + h)])} fill={top} />
    </g>
  );
}

/** A row of windows on the front-left face of a box. */
function Windows({ x, y, w, d, h, rows, cols, color }: { x: number; y: number; w: number; d: number; h: number; rows: number; cols: number; color: string }) {
  const out = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x0 = x + (c + 0.3) * (w / cols), x1 = x0 + 0.45 * (w / cols);
      const z0 = (r + 0.35) * (h / rows), z1 = z0 + 0.35 * (h / rows);
      out.push(<polygon key={`${r}-${c}`} points={pts([iso(x0, y + d, z0), iso(x1, y + d, z0), iso(x1, y + d, z1), iso(x0, y + d, z1)])} fill={color} />);
    }
  }
  return <g>{out}</g>;
}

type Status = "green" | "yellow" | "red";
const HAT: Record<Status, string> = { green: "#22a35a", yellow: "#f2b632", red: "#e0443a" };

interface Worker { id: string; name: string; x: number; y: number; status: Status; line1: string; line2: string }

const CREW: Worker[] = [
  { id: "maria", name: "Maria", x: 3.6, y: 5.9, status: "yellow", line1: "Danger in 12 min", line2: "Forecast says take a shade break" },
  { id: "jake", name: "Jake", x: 6.2, y: 6.6, status: "green", line1: "OK", line2: "No danger for 30 min" },
  { id: "dev", name: "Dev", x: 7.9, y: 3.4, status: "green", line1: "OK", line2: "Break in 20 min" },
  { id: "tasha", name: "Tasha", x: 1.4, y: 7.4, status: "green", line1: "OK", line2: "Just back from a break" },
];

function Person({ w, stormy, onHover, hovered }: { w: Worker; stormy: boolean; onHover: (id: string | null) => void; hovered: boolean }) {
  const [px, py] = iso(w.x, w.y, 0);
  const hat = HAT[w.status];
  return (
    <g transform={`translate(${px} ${py}) scale(1.55)`} onMouseEnter={() => onHover(w.id)} onMouseLeave={() => onHover(null)} style={{ cursor: "pointer" }}>
      <ellipse cx={0} cy={0} rx={9} ry={4} fill="rgba(40,30,20,.18)" />
      {w.status !== "green" && !stormy && (
        <circle cx={0} cy={-27} r={11} fill={hat} opacity={0.35} className="scene-pulse" />
      )}
      <rect x={-5} y={-21} width={10} height={20} rx={4} fill={stormy ? "#8a8f9c" : "#ff8a3d"} />
      <rect x={-5} y={-12} width={10} height={2.5} fill="#f4f1ea" opacity={0.9} />
      <circle cx={0} cy={-25} r={4.5} fill="#e9b892" />
      <path d="M -6.5 -26 a 6.5 6.5 0 0 1 13 0 z" fill={hat} />
      <rect x={-7.5} y={-26.5} width={15} height={2} rx={1} fill={hat} />
      {/* bigger invisible hit area */}
      <rect x={-14} y={-38} width={28} height={42} fill="transparent" />
      {hovered && (
        <g transform="translate(10 -44) scale(0.65)">
          <rect width={236} height={54} rx={12} fill="#1d1b19" opacity={0.94} />
          <circle cx={16} cy={18} r={5} fill={hat} />
          <text x={28} y={22} fill="#fff" fontSize={13} fontWeight={700} fontFamily="ui-sans-serif, system-ui">{w.name} · {stormy ? "Stop work" : w.line1}</text>
          <text x={14} y={40} fill="#cfc9c0" fontSize={11} fontFamily="ui-sans-serif, system-ui">{stormy ? "Lightning 4.8 mi. Shelter now." : w.line2}</text>
        </g>
      )}
    </g>
  );
}

function Tree({ x, y, s = 1 }: { x: number; y: number; s?: number }) {
  const [px, py] = iso(x, y, 0);
  return (
    <g transform={`translate(${px} ${py}) scale(${s})`}>
      <ellipse cx={0} cy={0} rx={10} ry={4.5} fill="rgba(40,30,20,.15)" />
      <rect x={-2} y={-14} width={4} height={14} fill="#8a6a4a" />
      <circle cx={0} cy={-22} r={11} fill="#7fae6a" />
      <circle cx={-5} cy={-26} r={7} fill="#93c07c" />
    </g>
  );
}

export function SiteScene() {
  const [stormy, setStormy] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);

  // Ten-mile ring around the plant (an ellipse in this view).
  const [rcx, rcy] = iso(4.5, 4.2, 0);
  const R = 6.2;

  return (
    <div className="relative select-none">
      <svg viewBox="0 0 810 660" className="h-auto w-full" role="img" aria-label="A power plant job site with four workers. Their hard hats show their heat status.">
        <defs>
          <radialGradient id="sun" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0%" stopColor="#ffd27a" />
            <stop offset="55%" stopColor="#ffb054" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#ffb054" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="slabSide" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#d9cdb6" />
            <stop offset="100%" stopColor="#c7b99e" />
          </linearGradient>
        </defs>

        {/* Click the sky to bring in a storm */}
        <rect x={0} y={0} width={810} height={150} fill="transparent" style={{ cursor: "pointer" }} onClick={() => setStormy(!stormy)} />

        {/* Sun with heat shimmer */}
        <g style={{ opacity: stormy ? 0.15 : 1, transition: "opacity .8s" }} pointerEvents="none">
          <circle cx={700} cy={70} r={70} fill="url(#sun)" />
          <circle cx={700} cy={70} r={26} fill="#ffcf6b" />
          {[0, 1, 2].map((i) => (
            <path key={i} className="scene-shimmer" style={{ animationDelay: `${i * 0.7}s` }}
              d={`M ${600 + i * 18} ${132 + i * 6} q 10 -8 20 0 t 20 0 t 20 0`} stroke="#f2a24a" strokeWidth={2} fill="none" strokeLinecap="round" opacity={0.5} />
          ))}
        </g>

        {/* Ground slab */}
        <g className="scene-in" style={{ animationDelay: "0.1s" }}>
        <Box x={-0.6} y={-0.6} z={-0.7} w={11.2} d={10.2} h={0.7} top="#ece4d3" left="url(#slabSide)" right="#cdbfa4" />
        {/* Grass patches and the road */}
        <polygon points={pts([iso(-0.6, 8.2), iso(10.6, 8.2), iso(10.6, 9.6), iso(-0.6, 9.6)])} fill="#cfe0b4" />
        <polygon points={pts([iso(9.1, -0.6), iso(10.6, -0.6), iso(10.6, 8.2), iso(9.1, 8.2)])} fill="#d6e6bc" />
        <polygon points={pts([iso(-0.6, 4.9), iso(9.1, 4.9), iso(9.1, 5.5), iso(-0.6, 5.5)])} fill="#d8cfbd" />
        </g>

        {/* 10-mile lightning ring (storm only) */}
        <ellipse cx={rcx} cy={rcy} rx={1.2247 * R * S} ry={0.7071 * R * S} fill="rgba(98,80,214,.08)" stroke="#6250d6" strokeWidth={2} strokeDasharray="7 7"
          style={{ opacity: stormy ? 1 : 0, transition: "opacity .6s" }} pointerEvents="none" />

        {/* Buildings, back to front (each drops into place on load) */}
        <g className="scene-drop" style={{ animationDelay: "0.45s" }}>
        <Box x={1} y={0.8} w={3.8} d={2.6} h={2.4} top="#f6f2ea" left="#ddd5c6" right="#cbc2b1" />
        <Windows x={1} y={0.8} w={3.8} d={2.6} h={2.4} rows={2} cols={6} color={stormy ? "#ffd88a" : "#b9c7d6"} />
        </g>
        <g className="scene-drop" style={{ animationDelay: "0.6s" }}>
        <Box x={5.2} y={1.1} w={2.2} d={2} h={1.5} top="#eef0f3" left="#cfd6df" right="#bcc5d0" />
        </g>
        <g className="scene-drop" style={{ animationDelay: "0.75s" }}>
        {/* Stacks with red and white bands */}
        {[1.5, 2.6].map((sx) => (
          <g key={sx}>
            <Box x={sx} y={3.7} w={0.55} d={0.55} h={4.6} top="#f1ede6" left="#e3ddd2" right="#d3ccbf" />
            <Box x={sx} y={3.7} z={3.9} w={0.55} d={0.55} h={0.35} top="#e0443a" left="#d23a31" right="#bd332b" />
            <Box x={sx} y={3.7} z={4.45} w={0.55} d={0.55} h={0.15} top="#e0443a" left="#d23a31" right="#bd332b" />
          </g>
        ))}
        </g>
        {/* Steam puffs */}
        {[1.5, 2.6].map((sx, i) => {
          const [px, py] = iso(sx + 0.27, 3.97, 4.9);
          return (
            <g key={sx} className="scene-steam" style={{ animationDelay: `${i * 1.3}s` }} pointerEvents="none">
              <circle cx={px} cy={py - 8} r={9} fill="#fff" opacity={0.85} />
              <circle cx={px + 10} cy={py - 18} r={12} fill="#fff" opacity={0.7} />
              <circle cx={px + 24} cy={py - 30} r={14} fill="#fff" opacity={0.5} />
            </g>
          );
        })}
        {/* Transformer yard: boxes, poles and wires */}
        <g className="scene-drop" style={{ animationDelay: "0.9s" }}>
        {[0, 1, 2].map((i) => [0, 1].map((j) => (
          <Box key={`${i}-${j}`} x={6.2 + i * 0.95} y={5.9 + j * 1.05} w={0.6} d={0.6} h={0.55} top="#b8c0c9" left="#9aa4af" right="#8a94a0" />
        )))}
        {[[5.9, 5.7], [8.9, 5.7], [5.9, 8], [8.9, 8]].map(([x, y]) => (
          <Box key={`${x}-${y}`} x={x} y={y} w={0.14} d={0.14} h={1.9} top="#7d6a55" left="#6c5a47" right="#5d4d3c" />
        ))}
        <g stroke="#5b5146" strokeWidth={1.2} fill="none" opacity={0.7} pointerEvents="none">
          <path d={`M ${iso(5.97, 5.77, 1.9).join(" ")} Q ${iso(7.4, 5.77, 1.5).join(" ")} ${iso(8.97, 5.77, 1.9).join(" ")}`} />
          <path d={`M ${iso(5.97, 8.07, 1.9).join(" ")} Q ${iso(7.4, 8.07, 1.5).join(" ")} ${iso(8.97, 8.07, 1.9).join(" ")}`} />
        </g>

        </g>
        <g className="scene-in" style={{ animationDelay: "1s" }}>
        <Tree x={0.2} y={9} />
        <Tree x={9.6} y={0.6} s={0.9} />
        <Tree x={9.8} y={2.4} s={1.1} />
        <Tree x={-0.2} y={6.2} s={0.85} />
        </g>

        {/* The crew */}
        {[...CREW].sort((a, b) => a.x + a.y - (b.x + b.y)).map((w, i) => (
          <g key={w.id} className="scene-pop" style={{ animationDelay: `${1.2 + i * 0.15}s` }}>
            <Person w={w} stormy={stormy} hovered={hovered === w.id} onHover={setHovered} />
          </g>
        ))}

        {/* Storm cloud, rain and lightning */}
        <g style={{ transform: stormy ? "translate(0,0)" : "translate(-520px,-40px)", opacity: stormy ? 1 : 0, transition: "transform 1.1s ease, opacity .8s" }} pointerEvents="none">
          <g fill="#6b7180">
            <circle cx={250} cy={72} r={34} /><circle cx={292} cy={58} r={42} /><circle cx={340} cy={74} r={32} /><circle cx={210} cy={86} r={24} />
            <rect x={196} y={74} width={170} height={36} rx={18} />
          </g>
          <g stroke="#8fb3e0" strokeWidth={2} strokeLinecap="round" opacity={0.7}>
            {Array.from({ length: 9 }, (_, i) => <line key={i} className="scene-rain" style={{ animationDelay: `${i * 0.11}s` }} x1={214 + i * 17} y1={118} x2={206 + i * 17} y2={140} />)}
          </g>
          <path className="scene-bolt" d="M 300 108 L 284 150 L 300 150 L 280 198 L 318 140 L 300 140 L 314 108 Z" fill="#ffd84d" stroke="#f2b632" strokeWidth={1.5} />
        </g>
      </svg>

      {/* Little caption, like a game hint */}
      <p className="pointer-events-none absolute bottom-1 right-3 text-xs text-[#7a7266]">
        {stormy ? "Stop work: lightning 4.8 mi. All clear in 30:00. Click the sky to clear it." : "Hover a worker. Click the sky to send a storm."}
      </p>
    </div>
  );
}
