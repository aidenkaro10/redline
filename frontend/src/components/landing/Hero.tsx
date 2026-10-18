"use client";

/**
 * The top of the selling page.
 *
 * 1. Intro: a lens irises open on a close-up of a worker's glowing wristband, and the camera
 *    pulls back to the whole job site (a 6 s ElevenLabs Veo clip). It lands on the hero picture.
 * 2. Hero: the miniature job site (ElevenLabs picture) fills the screen. Each worker has a
 *    floating status pill. Hover one to see their 30-minute forecast.
 * 3. Click anywhere on the scene: a storm rolls in. Lightning flash, a 10-mile ring,
 *    and every pill flips to STOP WORK while the workers move inside. Once the storm fills
 *    the view it stays 3 seconds, then clears by itself (or click again to clear it sooner).
 *
 * Motion style (same as Aiden's other sites): fast start, long soft landing (expo out),
 * no bounce, no blur. Only transform and opacity animate, so it stays at 60 fps.
 *
 * The day, storm and night pictures share one camera angle, so crossfading between them is seamless.
 */

import gsap from "gsap";

import { RedlineLogo } from "../RedlineLogo";
import { useEffect, useRef, useState } from "react";

export const SERIF = { fontFamily: '"Iowan Old Style", "New York", ui-serif, Georgia, serif' };
// Fade the picture's left and top edges to transparent so it melts into the page background
const FEATHER = "linear-gradient(to right, transparent, black 14%), linear-gradient(to bottom, transparent, black 10%)";

type Tone = "ok" | "warn" | "cool" | "stop";
const TONE: Record<Tone, { dot: string; pill: string }> = {
  ok:   { dot: "#2fa36b", pill: "bg-white/90 text-[#1d1b19]" },
  warn: { dot: "#f0a020", pill: "bg-[#fff3d6]/95 text-[#6b4300]" },
  cool: { dot: "#3b82f6", pill: "bg-white/90 text-[#1d1b19]" },
  stop: { dot: "#e5483b", pill: "bg-[#e5483b] text-white" },
};

/** Where each worker's head is in the picture (% of width, % of height), by day and in the storm. */
const WORKERS = [
  { name: "Jake R.",  job: "Pipe rack",    day: [44.4, 39.6],  storm: [68.25, 57.8], lift: 0,  tone: "warn" as Tone, label: "Danger in 14 min", now: 38.2, ahead: 38.9 },
  { name: "Maria L.", job: "Crane signal", day: [50.15, 54.6], storm: [65.9, 60.8], lift: 0,  tone: "ok" as Tone,   label: "Doing fine",       now: 37.4, ahead: 37.6 },
  { name: "Dev P.",   job: "Pipe haul",    day: [65.15, 68.1], storm: [75.4, 64.5], lift: 0,  tone: "ok" as Tone,   label: "Doing fine",       now: 37.6, ahead: 37.9 },
  { name: "Tasha W.", job: "On break",     day: [79.85, 62.3], storm: [77.75, 64.3], lift: 28, tone: "cool" as Tone, label: "Cooling down",     now: 37.9, ahead: 37.3 },
];

export function Hero({ night, setNight, onIntroDone, onSignup }: {
  night: boolean; setNight: (v: boolean) => void; onIntroDone: () => void; onSignup: () => void;
}) {
  const [intro, setIntro] = useState(true);
  const [storm, setStorm] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [left, setLeft] = useState(1800);       // storm countdown, seconds

  const introRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const lensRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const flashRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<HTMLDivElement>(null);
  const finished = useRef(false);
  const stormTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const dark = night || storm;

  // ---- 1. Intro: iris open, play the pull-back, fade into the page --------------------------
  useEffect(() => {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { finish(true); return; }      // plays on every load; people who turned off motion skip it

    const v = videoRef.current!;
    const tl = gsap.timeline();
    tl.fromTo(lensRef.current, { scale: 0.15, opacity: 0 }, { scale: 0.3, opacity: 1, duration: 0.6, ease: "power2.out" })
      .call(() => { v.play().catch(() => finish()); })
      .fromTo(v, { clipPath: "circle(6% at 50% 50%)", scale: 1.12 }, { clipPath: "circle(80% at 50% 50%)", scale: 1, duration: 1.6, ease: "expo.inOut" }, "<")
      .to(lensRef.current, { scale: 3.2, opacity: 0, duration: 1.6, ease: "expo.inOut" }, "<");
    const bail = setTimeout(() => finish(), 9000);   // if the video never plays, don't trap anyone
    // Scrolling, swiping or pressing a key skips the film (nobody likes being held back)
    const skip = () => finish();
    window.addEventListener("wheel", skip, { passive: true, once: true });
    window.addEventListener("touchmove", skip, { passive: true, once: true });
    window.addEventListener("keydown", skip, { once: true });
    return () => {
      tl.kill(); clearTimeout(bail);
      window.removeEventListener("wheel", skip); window.removeEventListener("touchmove", skip); window.removeEventListener("keydown", skip);
    };
  }, []);

  function finish(instant = false) {
    if (finished.current) return;
    finished.current = true;
    const done = () => { setIntro(false); onIntroDone(); }
    if (instant) done();
    else gsap.to(introRef.current, { opacity: 0, duration: 0.9, ease: "power2.inOut", onComplete: done });
    revealText(instant ? 0 : 0.35);
  }

  // Headline words slide up from behind an invisible line, one after another (no blur: it stutters)
  function revealText(delay: number) {
    const root = textRef.current;
    if (!root) return;
    gsap.set(root, { opacity: 1 });
    gsap.fromTo(root.querySelectorAll("[data-word]"), { yPercent: 105 },
      { yPercent: 0, duration: 1.2, stagger: 0.06, ease: "expo.out", delay, force3D: true });
    gsap.fromTo(root.querySelectorAll("[data-fade]"), { y: 24, opacity: 0 },
      { y: 0, opacity: 1, duration: 1.1, stagger: 0.09, ease: "expo.out", delay: delay + 0.45 });
    gsap.fromTo("[data-pin]", { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 1, stagger: 0.1, ease: "expo.out", delay: delay + 0.8 });
  }

  // ---- 3. Storm: two soft lightning flashes, holds 3 s once it fills the view, then clears ----
  const STORM_IN_MS = 1200;          // the storm picture's crossfade
  const STORM_HOLD_MS = 3000;        // how long it stays once fully in
  function toggleStorm(e: React.MouseEvent) {
    if ((e.target as HTMLElement).closest("a,button,input,select,[data-pin]")) return;
    const next = !storm;
    setStorm(next);
    setOpen(null);
    if (stormTimer.current) clearTimeout(stormTimer.current);
    if (next) {
      stormTimer.current = setTimeout(() => { setStorm(false); setOpen(null); }, STORM_IN_MS + STORM_HOLD_MS);
      setLeft(1800);
      gsap.timeline({ delay: 0.75 })
        .to(flashRef.current, { opacity: 0.55, duration: 0.06, ease: "power1.out" })
        .to(flashRef.current, { opacity: 0, duration: 0.18, ease: "power2.out" })
        .to(flashRef.current, { opacity: 0.35, duration: 0.06, ease: "power1.out" })
        .to(flashRef.current, { opacity: 0, duration: 0.9, ease: "expo.out" });
    }
  }

  useEffect(() => () => { if (stormTimer.current) clearTimeout(stormTimer.current); }, []);

  // "All clear in 30:00" ticks down while the storm is on
  useEffect(() => {
    if (!storm) return;
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [storm]);

  return (
    <section onClick={intro ? undefined : toggleStorm}
      className={`relative h-screen min-h-[640px] overflow-clip transition-colors duration-1000 ${dark ? "bg-[#0d1220] text-[#f4ede2]" : "bg-[#f4ede2] text-[#1d1b19]"} ${intro ? "" : "cursor-pointer"}`}>

      {/* The scene, edges feathered into the page color. Size lives in .hero-scene (globals.css):
          laptop = 92% of the screen tall, bottom right; phone = the lower half, centered on the plant.
          Pin positions are % of this box, so they line up at any size. */}
      <div ref={sceneRef} className="hero-scene absolute"
        style={{ contain: "layout paint", maskImage: FEATHER, WebkitMaskImage: FEATHER, maskComposite: "intersect", WebkitMaskComposite: "source-in" }}>
        <img src="/landing/hero.jpg" alt="A miniature Florida power plant job site with five workers" className="absolute inset-0 h-full w-full" />
        <img src="/landing/night.jpg" alt="" className={`absolute inset-0 h-full w-full transition-opacity duration-[1400ms] ${night && !storm ? "opacity-100" : "opacity-0"}`} />
        <img src="/landing/storm.jpg" alt="" className={`absolute inset-0 h-full w-full transition-opacity duration-[1200ms] ${storm ? "opacity-100" : "opacity-0"}`} />

        {/* Steam puffs drifting off the two stacks */}
        {!storm && [[74.5, 8.6], [83.6, 11.4]].map(([x, y], i) => (
          <div key={i} className="pointer-events-none absolute" style={{ left: `${x}%`, top: `${y}%` }}>
            {[0, 1.4, 2.8].map((d) => (
              <span key={d} className="puff absolute h-10 w-10 rounded-full bg-white/70 blur-md" style={{ animationDelay: `${d + i * 0.7}s` }} />
            ))}
          </div>
        ))}

        {/* 10-mile lightning ring around the plant */}
        <div className={`pointer-events-none absolute transition-opacity duration-700 ${storm ? "opacity-100 delay-700" : "opacity-0"}`} style={{ left: "65%", top: "58%" }}>
          {storm && [0, 0.8, 1.6].map((d) => (
            <span key={d} className="ripple absolute left-0 top-0 h-[56vw] w-[56vw] rounded-full border-2 border-[#ffd166]/70" style={{ animationDelay: `${d}s` }} />
          ))}
          <span className="absolute left-0 top-0 h-[56vw] w-[56vw] -translate-x-1/2 -translate-y-1/2 rounded-full border border-dashed border-[#ffd166]/60" />
          <span className="absolute left-0 top-[-28vw] -translate-x-1/2 -translate-y-full rounded-full bg-[#ffd166] px-2 py-0.5 text-[11px] font-bold text-[#3a2a00]">10 mi</span>
        </div>

        {/* Worker pins */}
        {WORKERS.map((w, i) => (
          <Pin key={w.name} w={w} i={i} storm={storm} open={open === i}
            onOpen={() => setOpen(i)} onClose={() => setOpen(null)} />
        ))}
      </div>

      {/* Moving rain + lightning flash on top of everything in the scene.
          The rain layer is taller than the screen and slides down (cheap), instead of repainting its background. */}
      {storm && <div className="rain-layer fade-in pointer-events-none absolute inset-x-0 bottom-0 top-[-480px]" />}
      <div ref={flashRef} className="pointer-events-none absolute inset-0 bg-white opacity-0" />
      {/* Soft wash so the headline always reads: from the top on phones, from the left on laptops */}
      <div className={`pointer-events-none absolute inset-x-0 top-0 h-[62%] bg-gradient-to-b md:inset-x-auto md:inset-y-0 md:left-0 md:h-auto md:w-[55%] md:bg-gradient-to-r ${dark ? "from-[#0d1220]/90 via-[#0d1220]/50 to-transparent" : "from-[#f4ede2] via-[#f4ede2]/80 to-transparent md:from-[#f4ede2]/80 md:via-[#f4ede2]/30"}`} />

      {/* Header */}
      <header className="relative z-20 mx-auto flex max-w-[1400px] items-center gap-4 px-5 py-4 md:gap-6 md:px-8 md:py-6">
        <a href="/preview" className="flex items-center gap-2.5 text-[15px] font-semibold tracking-tight">
          <Logo />
        </a>
        <nav className="ml-auto flex items-center gap-4 whitespace-nowrap text-sm md:gap-6">
          <a href="/worker" className="hidden opacity-70 transition-opacity hover:opacity-100 md:inline">Worker app</a>
          <a href="/signin" className="opacity-70 transition-opacity hover:opacity-100">Sign in</a>
          <button onClick={() => setNight(!night)} aria-label={night ? "Day" : "Night"}
            className={`hidden h-9 w-9 place-items-center rounded-full border backdrop-blur sm:grid ${dark ? "border-white/20 bg-white/10" : "border-black/10 bg-white/50"}`}>
            {night ? "☀" : "☾"}
          </button>
          <button onClick={onSignup} className={`rounded-full px-4 py-2 font-semibold ${dark ? "bg-[#f4ede2] text-[#1d1b19]" : "bg-[#1d1b19] text-white"}`}>Get started</button>
        </nav>
      </header>

      {/* Headline */}
      <div ref={textRef} className="pointer-events-none relative z-10 mx-auto flex max-w-[1400px] flex-col px-5 pt-6 opacity-0 md:h-[calc(100%-88px)] md:justify-center md:px-8 md:pb-16 md:pt-0">
        <p data-fade className="mb-4 text-[11px] font-semibold uppercase tracking-[0.22em] opacity-60 md:mb-5">Heat safety for outdoor crews · Florida</p>
        <h1 className="max-w-[560px] text-[clamp(38px,4.6vw,74px)] leading-[0.98] tracking-[-0.025em]" style={SERIF}>
          <Words text="Know who's overheating." />
          <span className="block italic"><Words text="Before they do." className="heat-text pr-[0.08em]" /></span>
        </h1>
        <p data-fade className="mt-4 max-w-[440px] text-[15px] leading-relaxed opacity-75 md:mt-6 md:text-[17px]">
          A wristband forecasts each worker&apos;s core temperature 30 minutes ahead and buzzes them before it gets dangerous.
          The crew lead sees who needs a break.
        </p>
        <div data-fade className="pointer-events-auto mt-6 flex items-center gap-3 md:mt-8">
          <button onClick={onSignup} className={`rounded-full px-6 py-3 text-sm font-semibold shadow-lg transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] hover:scale-[1.03] active:scale-[0.97] ${dark ? "bg-[#f4ede2] text-[#1d1b19]" : "bg-[#1d1b19] text-white"}`}>
            Get your crew on it →
          </button>
        </div>
      </div>

      {/* Hint chip / storm banner, bottom center */}
      <div className="pointer-events-none absolute inset-x-0 bottom-5 z-10 flex justify-center px-4 md:bottom-7">
        {storm ? (
          <div key="storm" className="flip-in flex items-center gap-2.5 whitespace-nowrap rounded-full bg-[#e5483b] px-4 py-2.5 text-[13px] font-semibold text-white shadow-xl md:gap-3 md:px-5 md:text-sm">
            <span className="h-2 w-2 animate-pulse rounded-full bg-white" />
            <span className="hidden sm:inline">Lightning 4 mi away. Everyone inside.</span><span className="sm:hidden">Lightning. Everyone inside.</span>
            <span className="tabular rounded-full bg-black/20 px-2.5 py-0.5">All clear in {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}</span>
          </div>
        ) : !intro && (
          <div key="hint" className="fade-in rounded-full border border-black/5 bg-white/70 px-4 py-2 text-xs font-medium text-[#5d564d] shadow-sm backdrop-blur" style={{ animationDelay: "2.2s" }}>
            <span className="hidden md:inline">Hover a worker · Click anywhere to send a storm</span>
            <span className="md:hidden">Tap a worker · Tap the sky for a storm</span>
          </div>
        )}
      </div>

      {/* The intro lens. Sits on top until the pull-back finishes. */}
      {intro && (
        <div ref={introRef} className="fixed inset-0 z-50 grid place-items-center bg-black" onClick={() => finish()}>
          <video ref={videoRef} src="/landing/flyout.mp4" poster="/landing/flyout-poster.jpg" muted playsInline preload="auto"
            onEnded={() => finish()} onError={() => finish()}
            className="absolute inset-0 h-full w-full object-cover" style={{ clipPath: "circle(0% at 50% 50%)" }} />
          <div ref={lensRef} className="pointer-events-none absolute h-[60vmin] w-[60vmin] rounded-full border border-[#f4ede2]/60 opacity-0 shadow-[0_0_0_1px_rgba(244,237,226,.15),inset_0_0_80px_rgba(255,176,84,.25)]" />
          <button onClick={() => finish()} className="absolute bottom-6 right-6 rounded-full border border-white/20 px-4 py-1.5 text-xs font-semibold text-white/80 hover:bg-white/10">Skip</button>
        </div>
      )}
    </section>
  );
}

/** Splits a line into words that GSAP can lift one by one. */
function Words({ text, className = "" }: { text: string; className?: string }) {
  return (
    <span className="block">
      {text.split(" ").map((w, i) => (
        <span key={i} className="inline-block overflow-hidden pb-[0.08em] align-bottom">
          <span data-word className={`inline-block ${className}`}>{w}&nbsp;</span>
        </span>
      ))}
    </span>
  );
}

/** One worker: a bobbing status pill over their head. Hover (or tap) to open their forecast card. */
function Pin({ w, i, storm, open, onOpen, onClose }: {
  w: (typeof WORKERS)[number]; i: number; storm: boolean; open: boolean; onOpen: () => void; onClose: () => void;
}) {
  const [x, y] = storm ? w.storm : w.day;
  const tone: Tone = storm ? "stop" : w.tone;
  const t = TONE[tone];
  // In the storm the pills flip one after another (dominoes), then the workers walk inside
  const move = storm ? 1.1 + i * 0.22 : i * 0.08;
  return (
    <div data-pin onMouseEnter={onOpen} onMouseLeave={onClose} onClick={open ? onClose : onOpen}
      className="absolute z-10" style={{ left: `${x}%`, top: `${y}%`, transition: `left 1.6s cubic-bezier(.6,0,.2,1) ${move}s, top 1.6s cubic-bezier(.6,0,.2,1) ${move}s` }}>
      {/* the worker's wristband LED */}
      <span className="led absolute left-0 top-[18px] h-2.5 w-2.5 rounded-full" style={{ background: t.dot, boxShadow: `0 0 12px 3px ${t.dot}` }} />
      <div className="bob absolute left-0 top-0" style={{ animationDelay: `${i * 0.4}s` }}>
        <div key={tone} className={`flip-in flex cursor-default items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-semibold shadow-lg ${t.pill}`}
          style={{ animationDelay: storm ? `${0.6 + i * 0.22}s` : "0s", marginTop: storm ? -w.lift : 0 }}>
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: tone === "stop" ? "white" : t.dot }} />
          {w.name.split(" ")[0]} · {storm ? "STOP WORK" : w.label}
        </div>
        {open && <ForecastCard w={w} storm={storm} />}
      </div>
    </div>
  );
}

/** The card that opens on hover: now vs 30 min ahead, with a small forecast line that draws itself. */
function ForecastCard({ w, storm }: { w: (typeof WORKERS)[number]; storm: boolean }) {
  const up = w.ahead > w.now;
  // Mini chart: last 20 min (solid) then the next 30 (dashed), warning line at 38.5
  const y = (c: number) => 50 - (c - 36.9) * 22;
  const past = `M4 ${y(w.now - (up ? 0.5 : -0.4))} C 40 ${y(w.now - (up ? 0.35 : -0.3))}, 60 ${y(w.now - 0.1)}, 80 ${y(w.now)}`;
  const next = `M80 ${y(w.now)} C 110 ${y((w.now + w.ahead) / 2)}, 140 ${y(w.ahead - 0.05)}, 196 ${y(w.ahead)}`;
  return (
    <div className="card-in absolute left-1/2 top-[-8px] w-[232px] -translate-x-1/2 -translate-y-full rounded-2xl border border-white/60 bg-white/95 p-3.5 text-[#1d1b19] shadow-2xl">
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-semibold">{w.name}</p>
        <p className="text-[11px] text-[#8a8174]">{storm ? "Sheltering" : w.job}</p>
      </div>
      <svg viewBox="0 0 200 60" className="mt-2 h-14 w-full overflow-visible">
        <line x1="0" x2="200" y1={y(38.5)} y2={y(38.5)} stroke="#f0a020" strokeDasharray="3 3" strokeWidth="1" />
        <text x="198" y={y(38.5) - 3} textAnchor="end" fontSize="8" fill="#b07800">warning</text>
        <line x1="80" x2="80" y1="0" y2="60" stroke="#1d1b19" strokeOpacity=".2" />
        <path d={past} fill="none" stroke="#2a78d6" strokeWidth="2.2" className="draw" />
        <path d={next} fill="none" stroke="#2a78d6" strokeWidth="2" strokeDasharray="4 3" opacity=".7" />
      </svg>
      <div className="mt-1 grid grid-cols-2 gap-2 text-[11px] text-[#5d564d]">
        <p>Core now<br /><b className="tabular text-[15px] text-[#1d1b19]">{w.now.toFixed(1)} °C</b></p>
        <p>In 30 min<br /><b className="tabular text-[15px]" style={{ color: w.ahead >= 38.5 ? "#c26a00" : "#1d1b19" }}>{w.ahead.toFixed(1)} °C</b></p>
      </div>
      <p className="mt-2 rounded-lg bg-[#f4ede2] px-2 py-1.5 text-[11px] leading-snug text-[#5d564d]">
        {storm ? "Band says: stop work, get inside." : w.tone === "warn" ? "Band buzzed: take a break in the shade." : w.tone === "cool" ? "On break. Cooling off nicely." : "No action needed."}
      </p>
    </div>
  );
}

/** The Redline logo (tile + RED LINE wordmark). Kept under this name so every page imports it the same way. */
export function Logo({ size }: { size?: number }) {
  return <RedlineLogo size={size} />;
}
