"use client";

/**
 * The three scroll beats under the hero. Each one pins to the screen while you scroll and
 * plays a GSAP timeline tied to the scroll position (scrub), like a little film you control.
 *
 *   BandBeat:    the wristband close-up + a core temp chart that draws itself. The forecast
 *                crosses the warning line, and the band buzzes 18 minutes early.
 *   CrewBeat:    the crew lead's phone. Rows re-sort worst first, a notification drops in.
 *   FloridaBeat: 79 real power plant sites drop onto Florida, plus the sign-up card.
 */

import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { forwardRef, useEffect, useRef, useState } from "react";

import { OUTLINE, PLANTS } from "./florida";
import { SERIF } from "./Hero";

gsap.registerPlugin(ScrollTrigger);

function Label({ n, text }: { n: string; text: string }) {
  return <p className="text-[11px] font-semibold uppercase tracking-[0.22em] opacity-55">{n} · {text}</p>;
}

// ---------------------------------------------------------------------------------------------
// 01 The band
// ---------------------------------------------------------------------------------------------

// Chart geometry: x = minutes (-40 to +30), y = core temp (36.8 to 39.4 °C)
const X = (t: number) => 40 + ((t + 40) / 70) * 520;
const Y = (c: number) => 280 - ((c - 36.8) / 2.6) * 250;
const pastC = (t: number) => 37.1 + 0.9 * Math.pow((t + 40) / 40, 1.6) + 0.025 * Math.sin(t * 0.35);
const nextC = (t: number) => 38.0 + 0.8 * Math.pow(t / 30, 0.9);
const spread = (t: number) => 0.04 + 0.3 * (t / 30);
const path = (pts: [number, number][]) => pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
const range = (a: number, b: number, n = 40) => Array.from({ length: n + 1 }, (_, i) => a + ((b - a) * i) / n);
const PAST = path(range(-40, 0).map((t) => [X(t), Y(pastC(t))]));
const NEXT = path(range(0, 30).map((t) => [X(t), Y(nextC(t))]));
const BAND = path([...range(0, 30).map((t) => [X(t), Y(nextC(t) + spread(t))] as [number, number]),
  ...range(30, 0).map((t) => [X(t), Y(nextC(t) - spread(t))] as [number, number])]) + " Z";
const CROSS = 17.8;                               // minutes until the forecast crosses the warning line

export function BandBeat() {
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    const ctx = gsap.context(() => {
      const q = gsap.utils.selector(root);
      const len = (el: SVGPathElement) => el.getTotalLength();
      const past = root.current!.querySelector<SVGPathElement>("[data-past]")!;
      const next = root.current!.querySelector<SVGPathElement>("[data-next]")!;
      gsap.set(past, { strokeDasharray: len(past), strokeDashoffset: len(past) });
      gsap.set(next, { strokeDasharray: len(next), strokeDashoffset: len(next) });

      // Words and the chart card rise as the section comes into view...
      gsap.from([...q("[data-copy] > *"), ...q("[data-chart]")], { y: 36, opacity: 0, stagger: 0.08, duration: 1.2, ease: "expo.out",
        scrollTrigger: { trigger: root.current, start: "top 65%" } });
      // ...then the section pins and the scroll wheel plays the chart like a film
      const tl = gsap.timeline({
        scrollTrigger: { trigger: root.current, start: "top top", end: "+=1800", scrub: 1.2, pin: true, anticipatePin: 1 },
      });
      tl.fromTo(q("[data-photo]"), { scale: 1.35, clipPath: "inset(18% 22% 18% 22% round 40px)" }, { scale: 1, clipPath: "inset(0% 0% 0% 0% round 28px)", duration: 1.2, ease: "power2.inOut" })
        .to(past, { strokeDashoffset: 0, duration: 1.2, ease: "none" })
        .from(q("[data-now]"), { opacity: 0, duration: 0.2 })
        .fromTo(q("[data-band-clip]"), { attr: { width: 0 } }, { attr: { width: 270 }, duration: 1.2, ease: "none" })
        .to(next, { strokeDashoffset: 0, duration: 1.2, ease: "none" }, "<")
        .fromTo(q("[data-warn]"), { opacity: 0.25 }, { opacity: 1, duration: 0.3 })
        .from(q("[data-cross]"), { scale: 0, transformOrigin: "center", duration: 0.4, ease: "expo.out" })
        .from(q("[data-callout]"), { y: 16, opacity: 0, duration: 0.5, ease: "expo.out" }, "<")
        .to(q("[data-led]"), { opacity: 1, scale: 1, duration: 0.3 }, "<")
        .to({}, { duration: 0.8 });            // hold the finished frame for a moment
    }, root);
    return () => ctx.revert();
  }, []);

  return (
    <section ref={root} className="relative flex h-screen items-center overflow-clip bg-[#f4ede2] text-[#1d1b19]">
      <div className="mx-auto grid w-full max-w-[1400px] items-center gap-12 px-8 lg:grid-cols-[1.15fr_1fr]">
        <div className="relative aspect-[16/9] w-full">
          <div data-photo className="absolute inset-0 overflow-hidden rounded-[28px] shadow-[0_40px_80px_-30px_rgba(60,40,20,.45)]">
            <img src="/landing/wrist.jpg" alt="A worker's wristband with a glowing amber light" className="h-full w-full object-cover" />
            <span data-led className="absolute h-16 w-16 rounded-full opacity-0" style={{ left: "76%", top: "51%", transform: "translate(-50%,-50%) scale(.4)", background: "radial-gradient(circle, rgba(255,170,40,.9), rgba(255,140,0,0) 70%)" }} />
          </div>
        </div>

        <div className="flex flex-col gap-6">
          <div data-copy className="flex flex-col gap-3">
            <Label n="01" text="The band" />
            <h2 className="text-[clamp(34px,3.6vw,56px)] leading-[1.02] tracking-[-0.02em]" style={SERIF}>30 minutes of <i className="heat-text">warning.</i></h2>
            <p className="max-w-[460px] text-[16px] leading-relaxed opacity-70">
              Heart rate and skin temperature go in. A forecast of core temperature comes out, with a range.
              When the forecast crosses the line, the band buzzes. Not when it&apos;s already too late.
            </p>
          </div>

          <div data-chart className="relative rounded-3xl border border-black/5 bg-white/70 p-4 shadow-[0_20px_60px_-25px_rgba(60,40,20,.35)] backdrop-blur">
            <svg viewBox="0 0 600 300" className="w-full overflow-visible">
              <defs><clipPath id="band-clip"><rect data-band-clip x={X(0)} y="0" width="0" height="300" /></clipPath></defs>
              {[37, 37.5, 38, 38.5, 39].map((c) => <line key={c} x1="40" x2="560" y1={Y(c)} y2={Y(c)} stroke="#1d1b19" strokeOpacity=".06" />)}
              {[37, 38, 39].map((c) => <text key={c} x="30" y={Y(c) + 4} textAnchor="end" fontSize="11" fill="#8a8174">{c}°</text>)}
              <g data-warn>
                <line x1="40" x2="560" y1={Y(38.5)} y2={Y(38.5)} stroke="#f0a020" strokeWidth="1.5" strokeDasharray="6 5" />
                <text x="560" y={Y(38.5) - 7} textAnchor="end" fontSize="11" fontWeight="600" fill="#b07800">warning</text>
              </g>
              <line x1="40" x2="560" y1={Y(39)} y2={Y(39)} stroke="#e5483b" strokeWidth="1.5" />
              <text x="560" y={Y(39) - 7} textAnchor="end" fontSize="11" fontWeight="700" fill="#e5483b">danger</text>
              <path d={BAND} fill="#2a78d6" fillOpacity=".18" clipPath="url(#band-clip)" />
              <path data-past d={PAST} fill="none" stroke="#2a78d6" strokeWidth="3" strokeLinecap="round" />
              <path data-next d={NEXT} fill="none" stroke="#2a78d6" strokeWidth="2.5" strokeDasharray="7 5" />
              <g data-now>
                <line x1={X(0)} x2={X(0)} y1="20" y2="285" stroke="#1d1b19" strokeOpacity=".35" />
                <text x={X(0)} y="14" textAnchor="middle" fontSize="11" fontWeight="600" fill="#1d1b19">now</text>
              </g>
              <circle data-cross cx={X(CROSS)} cy={Y(38.5)} r="7" fill="#f0a020" stroke="white" strokeWidth="3" />
              {[-40, -20, 0, 15, 30].map((t) => <text key={t} x={X(t)} y="298" textAnchor="middle" fontSize="11" fill="#8a8174">{t === 0 ? "" : t > 0 ? `+${t} min` : `${t} min`}</text>)}
            </svg>
            <div data-callout className="absolute right-6 top-6 rounded-2xl bg-[#1d1b19] px-4 py-2.5 text-white shadow-xl">
              <p className="text-[11px] uppercase tracking-[0.18em] text-white/60">Band buzzes now</p>
              <p className="text-lg font-semibold">Danger in 18 min</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------
// 02 The crew lead
// ---------------------------------------------------------------------------------------------

const CREW = [
  { name: "Dev Patel",   job: "Pipe haul",    rank: 2 },
  { name: "Jake Rivera", job: "Pipe rack",    rank: 0 },
  { name: "Maria Lopez", job: "Crane signal", rank: 3 },
  { name: "Tasha Webb",  job: "On break",     rank: 1 },
];
const ROW = 68;

export function CrewBeat() {
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    const ctx = gsap.context(() => {
      const q = gsap.utils.selector(root);
      gsap.from(q("[data-copy] > *"), { y: 36, opacity: 0, stagger: 0.08, duration: 1.2, ease: "expo.out",
        scrollTrigger: { trigger: root.current, start: "top 65%" } });
      const tl = gsap.timeline({ scrollTrigger: { trigger: root.current, start: "top 40%", end: "+=2000", scrub: 1.2 } });
      ScrollTrigger.create({ trigger: root.current, start: "top top", end: "+=1600", pin: true, anticipatePin: 1 });
      tl.fromTo(q("[data-phone]"), { y: 220, rotate: 3, opacity: 0 }, { y: 0, rotate: 0, opacity: 1, duration: 1.2, ease: "expo.out" })
        .to(q("[data-ok='Jake Rivera']"), { opacity: 0, y: -6, duration: 0.35 }, "+=0.3")
        .from(q("[data-bad='Jake Rivera']"), { opacity: 0, y: 6, duration: 0.45, ease: "expo.out" }, "<")
        .to(q("[data-ok='Tasha Webb']"), { opacity: 0, y: -6, duration: 0.35 }, "<0.15")
        .from(q("[data-bad='Tasha Webb']"), { opacity: 0, y: 6, duration: 0.45, ease: "expo.out" }, "<");
      // Re-sort: worst first. Each row slides from its old slot to its new one.
      CREW.forEach((c, i) => tl.to(q(`[data-row='${c.name}']`), { y: (c.rank - i) * ROW, duration: 0.9, ease: "expo.inOut" }, i ? "<" : "+=0.2"));
      tl.fromTo(q("[data-note]"), { y: -60, opacity: 0 }, { y: 0, opacity: 1, duration: 0.7, ease: "expo.out" }, "+=0.15")
        .to({}, { duration: 0.8 });
    }, root);
    return () => ctx.revert();
  }, []);

  return (
    <section ref={root} className="relative flex h-screen items-center overflow-clip bg-[#ebe3d6] text-[#1d1b19]">
      <div className="mx-auto grid w-full max-w-[1200px] items-center gap-16 px-8 lg:grid-cols-[1fr_auto]">
        <div data-copy className="flex flex-col gap-3">
          <Label n="02" text="The crew lead" />
          <h2 className="text-[clamp(34px,3.6vw,56px)] leading-[1.02] tracking-[-0.02em]" style={SERIF}>See who needs <i className="heat-text">a break.</i></h2>
          <p className="max-w-[440px] text-[16px] leading-relaxed opacity-70">
            The crew lead&apos;s phone sorts the crew worst first. No vitals, no medical data. Just who&apos;s fine,
            who needs a break and when.
          </p>
          <p className="max-w-[440px] text-[13px] leading-relaxed opacity-50">Safety calls come from fixed rules, never from AI.</p>
        </div>

        <div data-phone className="relative h-[600px] w-[296px] rounded-[48px] bg-[#16161a] p-[10px] shadow-[0_50px_100px_-30px_rgba(30,20,10,.6)]">
          <div className="relative h-full overflow-hidden rounded-[39px] bg-[#f7f7f5]">
            <div className="absolute left-1/2 top-2.5 h-6 w-24 -translate-x-1/2 rounded-full bg-[#16161a]" />
            <div className="flex justify-between px-7 pt-3 text-[11px] font-semibold"><span>2:41</span><span>●●● ▮</span></div>
            <div className="px-5 pt-6">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-[#8a8174]">Crew</p>
              <p className="text-xl font-bold">Deerhaven</p>
              <p className="text-xs text-[#6b6358]">4 workers · Feels like 108 °F</p>
            </div>
            <div className="relative mx-3 mt-4" style={{ height: ROW * 4 }}>
              {CREW.map((c, i) => (
                <div key={c.name} data-row={c.name} className="absolute inset-x-0 flex h-[60px] items-center gap-3 rounded-2xl bg-white px-3 shadow-sm" style={{ top: i * ROW }}>
                  <span className="grid h-9 w-9 place-items-center rounded-full bg-[#eee] text-xs font-bold">{c.name.split(" ").map((s) => s[0]).join("")}</span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">{c.name}</p>
                    <p className="text-[11px] text-[#8a8174]">{c.job}</p>
                  </div>
                  <span className="relative [perspective:400px]">
                    <span data-ok={c.name} className="block rounded-full bg-[#e3f4ea] px-2 py-1 text-[10px] font-bold text-[#1d7a4a]">OK</span>
                    {c.rank < 2 && (
                      <span data-bad={c.name} className={`absolute right-0 top-0 block whitespace-nowrap rounded-full px-2 py-1 text-[10px] font-bold ${c.rank === 0 ? "bg-[#fde9c4] text-[#7a4b00]" : "bg-[#e0ebff] text-[#1f4fa8]"}`}>
                        {c.rank === 0 ? "Break in 14m" : "Cooling"}
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
            <div data-note className="absolute inset-x-3 top-10 rounded-2xl bg-white/95 p-3 opacity-0 shadow-xl backdrop-blur">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-[#8a8174]">PlanIT · now</p>
              <p className="text-[13px] font-semibold">Jake needs a break in 14 min</p>
              <p className="text-[11px] text-[#6b6358]">His band already buzzed. Shade tent is open.</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------
// 03 Florida + sign-up
// ---------------------------------------------------------------------------------------------

// Simple flat map projection that keeps Florida's shape (squash longitude by cos 28°)
const K = 72;
const px = (lon: number) => 14 + (lon + 87.7) * Math.cos((28 * Math.PI) / 180) * K;
const py = (lat: number) => 14 + (31.1 - lat) * K;
const COAST = OUTLINE.map(([lo, la], i) => `${i ? "L" : "M"}${px(lo).toFixed(1)} ${py(la).toFixed(1)}`).join(" ") + " Z";
// A sample "tomorrow's heat risk" per site: hotter further south, plus some per-site spread
const risk = (lat: number, i: number) => Math.min(1, Math.max(0, 0.3 + (30.5 - lat) * 0.1 + (((i * 37) % 11) / 11) * 0.35));
const riskColor = (r: number) => (r > 0.72 ? "#ff5a3d" : r > 0.45 ? "#ffb02e" : "#52d18a");
const SITES = PLANTS.map(([lon, lat, name], i) => ({ x: px(lon), y: py(lat), name, r: risk(lat, i) })).sort((a, b) => a.y - b.y);
const GNV = { x: px(-82.32), y: py(29.65) };       // Gainesville, where a new sign-up lands

export const FloridaBeat = forwardRef<HTMLElement>(function FloridaBeat(_, ref) {
  const mapRef = useRef<SVGSVGElement>(null);
  const [joined, setJoined] = useState<string | null>(null);
  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from("[data-site]", {
        y: -14, opacity: 0, duration: 1, stagger: 0.018, ease: "expo.out",
        scrollTrigger: { trigger: mapRef.current, start: "top 75%" },
      });
      gsap.from("[data-coast]", { opacity: 0, duration: 1.6, ease: "power2.out", scrollTrigger: { trigger: mapRef.current, start: "top 80%" } });
    }, mapRef);
    return () => ctx.revert();
  }, []);

  return (
    <section ref={ref} id="signup" className="relative overflow-clip bg-[#0d1220] py-28 text-[#f4ede2]">
      <div aria-hidden className="pointer-events-none absolute -right-40 top-0 h-[600px] w-[600px] rounded-full bg-[#ff7a3d] opacity-[0.07] blur-3xl" />
      <div className="mx-auto grid max-w-[1300px] items-center gap-16 px-8 lg:grid-cols-[1.1fr_1fr]">
        <div className="relative">
          <svg ref={mapRef} viewBox="0 0 560 470" className="w-full overflow-visible">
            <defs>
              <radialGradient id="glow"><stop offset="0" stopColor="#f4ede2" stopOpacity=".09" /><stop offset="1" stopColor="#f4ede2" stopOpacity="0" /></radialGradient>
            </defs>
            <path data-coast d={COAST} fill="url(#glow)" stroke="#f4ede2" strokeOpacity=".28" strokeWidth="1.2" strokeLinejoin="round" />
            {SITES.map((s, i) => (
              <g key={i} data-site>
                {s.r > 0.8 && <circle cx={s.x} cy={s.y} r="4" fill={riskColor(s.r)} className="dot-pulse" style={{ animationDelay: `${(i % 5) * 0.3}s` }} />}
                <circle cx={s.x} cy={s.y} r="4" fill={riskColor(s.r)} stroke="#0d1220" strokeWidth="1.5"><title>{s.name}</title></circle>
              </g>
            ))}
            {joined && (
              <g className="pin-drop">
                <circle cx={GNV.x} cy={GNV.y} r="5" fill="none" stroke="#f4ede2" className="dot-pulse" />
                <path d={`M${GNV.x} ${GNV.y} l-9 -16 a10 10 0 1 1 18 0 z`} fill="#f4ede2" />
                <circle cx={GNV.x} cy={GNV.y - 21} r="3.5" fill="#0d1220" />
                <rect x={GNV.x + 14} y={GNV.y - 36} rx="9" height="22" width={Math.max(70, joined.length * 7.2 + 20)} fill="#f4ede2" />
                <text x={GNV.x + 24} y={GNV.y - 21} fontSize="12" fontWeight="700" fill="#0d1220">{joined}</text>
              </g>
            )}
          </svg>
          <div className="mt-2 flex gap-5 text-[11px] text-white/55">
            {[["#52d18a", "Low"], ["#ffb02e", "Elevated"], ["#ff5a3d", "High"]].map(([c, t]) => (
              <span key={t} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: c }} />{t}</span>
            ))}
            <span className="ml-auto">Sample day. 79 real power plant sites.</span>
          </div>
        </div>

        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-3">
            <Label n="03" text="Every site" />
            <h2 className="text-[clamp(34px,3.6vw,56px)] leading-[1.02] tracking-[-0.02em]" style={SERIF}>Tomorrow&apos;s heat, <i className="heat-text">every site.</i></h2>
            <p className="max-w-[460px] text-[16px] leading-relaxed text-white/65">
              Heat, lightning, flooding and fire risk for every plant in Florida, so you plan the hard work for the cool hours.
            </p>
          </div>
          <SignupCard onDone={setJoined} />
        </div>
      </div>

      <footer className="mx-auto mt-24 flex max-w-[1300px] items-center justify-between px-8 text-xs text-white/40">
        <span>PlanIT Control · Built at SwampHacks XII</span>
        <span>Pictures and film made with ElevenLabs</span>
      </footer>
    </section>
  );
});

/** "Get your crew on it": sign-up for an organization. Preview only, nothing is sent. */
function SignupCard({ onDone }: { onDone: (company: string | null) => void }) {
  const [done, setDone] = useState<string | null>(null);
  const [form, setForm] = useState({ company: "", name: "", email: "", size: "11-50" });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });
  const input = "rounded-xl border border-white/15 bg-white/[0.06] px-3.5 py-3 text-sm text-white outline-none placeholder:text-white/40 focus:border-[#ff9a5a]";

  return (
    <div className="w-full max-w-[480px] [perspective:900px]">
      <div className="rounded-3xl border border-white/10 bg-white/[0.06] p-6 shadow-[0_30px_80px_-30px_rgba(0,0,0,.7)] backdrop-blur-xl">
        {done ? (
          <div key="done" className="flip-in py-3">
            <p className="text-3xl tracking-tight" style={SERIF}>You&apos;re on the list.</p>
            <p className="mt-2 text-sm text-white/65">Thanks, {done}. We&apos;ll reach out to set up your first crew. Your pin is on the map.</p>
            <div className="mt-5 flex items-center gap-4">
              <a href="/signin" className="rounded-full bg-[#f4ede2] px-5 py-2.5 text-sm font-semibold text-[#1d1b19] transition-transform hover:scale-[1.02]">Open the dashboard →</a>
              <button onClick={() => { setDone(null); onDone(null); }} className="text-xs font-semibold text-[#ff9a5a]">Sign up another team</button>
            </div>
          </div>
        ) : (
          <form key="form" className="flex flex-col gap-3"
            onSubmit={(e) => { e.preventDefault(); setDone(form.name.split(" ")[0] || form.company); onDone(form.company); }}>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-white/50">Get your crew on it</p>
            <div className="grid grid-cols-2 gap-2.5">
              <input required value={form.company} onChange={set("company")} placeholder="Company" className={input} />
              <input required value={form.name} onChange={set("name")} placeholder="Your name" className={input} />
            </div>
            <div className="grid grid-cols-[1fr_auto] gap-2.5">
              <input required type="email" value={form.email} onChange={set("email")} placeholder="Work email" className={input} />
              <select value={form.size} onChange={set("size")} className={`${input} [&>option]:text-black`} aria-label="Crew size">
                <option value="1-10">1-10 workers</option>
                <option value="11-50">11-50 workers</option>
                <option value="51-200">51-200 workers</option>
                <option value="200+">200+ workers</option>
              </select>
            </div>
            <button className="mt-1 rounded-full bg-[#f4ede2] px-4 py-3 text-sm font-semibold text-[#1d1b19] transition-transform hover:scale-[1.02]">
              Request access →
            </button>
            <p className="text-center text-[11px] text-white/35">Preview: nothing is sent yet.</p>
          </form>
        )}
      </div>
    </div>
  );
}
