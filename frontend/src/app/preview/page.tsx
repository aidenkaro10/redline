"use client";

/**
 * The selling page (laptop): what PlanIT Control does, a live-looking scene of a
 * job site, and a sign-up card for organizations. One screen.
 *
 * PREVIEW: the sign-up form doesn't send anything yet. The phone apps (/worker, /)
 * stay plain and serious; this page is the fun one.
 */

import Link from "next/link";
import { useState } from "react";

import { SiteScene } from "@/components/landing/SiteScene";

const SERIF = { fontFamily: 'ui-serif, "New York", "Iowan Old Style", Georgia, serif' };

export default function SellingPage() {
  return (
    <div className="relative min-h-screen overflow-hidden bg-[#f7f1e7] text-[#1d1b19]">
      {/* Soft color blobs, like the rest of Aiden's sites */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute -left-40 -top-40 h-[520px] w-[520px] rounded-full bg-[#ffd9bf] opacity-70 blur-3xl" />
        <div className="absolute right-[-120px] top-[-80px] h-[480px] w-[480px] rounded-full bg-[#e3dcff] opacity-60 blur-3xl" />
        <div className="absolute bottom-[-160px] left-1/3 h-[420px] w-[420px] rounded-full bg-[#d9f0dc] opacity-60 blur-3xl" />
      </div>

      <header className="fade-in relative z-10 mx-auto flex max-w-[1320px] items-center gap-6 px-8 py-5">
        <Link href="/preview" className="flex items-center gap-2.5 font-semibold tracking-tight">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-[#1d1b19]">
            <svg viewBox="0 0 28 28" className="h-5 w-5" aria-hidden><path d="M7 18c2-6 4-9 6-9s3 5 6 5" stroke="#ffb054" strokeWidth="2.6" fill="none" strokeLinecap="round" /><circle cx="19" cy="14" r="2.2" fill="#ffb054" /></svg>
          </span>
          PlanIT Control
        </Link>
        <nav className="ml-auto flex items-center gap-6 text-sm text-[#5d564d]">
          <Link href="/worker" className="hover:text-[#1d1b19]">Worker app</Link>
          <Link href="/" className="hover:text-[#1d1b19]">Crew lead app</Link>
          <a href="#signup" className="rounded-full bg-[#1d1b19] px-4 py-2 font-semibold text-white hover:opacity-90">Get started</a>
        </nav>
      </header>

      <main className="relative z-10 mx-auto grid min-h-[calc(100vh-76px)] max-w-[1320px] items-center gap-4 px-8 pb-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <section className="flex flex-col gap-5">
          <span className="rise w-fit rounded-full border border-[#e4d8c6] bg-white/60 px-3 py-1 text-xs text-[#6b6358]" style={{ animationDelay: "0.05s" }}>
            <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-[#ff8a3d] align-middle" />
            For outdoor crews working in Florida heat
          </span>
          <h1 className="text-[clamp(42px,4.6vw,66px)] leading-[1] tracking-[-0.02em]" style={SERIF}>
            <span className="rise block" style={{ animationDelay: "0.15s" }}>Know who&apos;s overheating.</span>
            <span className="rise block italic text-[#e8663d]" style={{ animationDelay: "0.35s" }}>Before they do.</span>
          </h1>
          <p className="rise max-w-[520px] text-[17px] leading-relaxed text-[#5d564d]" style={{ animationDelay: "0.55s" }}>
            A wristband forecasts each worker&apos;s core temperature 30 minutes ahead and buzzes them before it gets
            dangerous. Crew leads see who needs a break, plus lightning, flooding and fire at every site.
          </p>
          <dl className="rise grid max-w-[540px] grid-cols-3 gap-4" style={{ animationDelay: "0.7s" }}>
            <Stat big="2x" text="more accurate than the Army's heat model, 20 minutes ahead" />
            <Stat big="30 min" text="of warning before a worker hits danger" />
            <Stat big="0" text="safety calls made by AI. Fixed rules decide." />
          </dl>
          <SignupCard />
        </section>

        <section className="relative">
          <SiteScene />
        </section>
      </main>
    </div>
  );
}

function Stat({ big, text }: { big: string; text: string }) {
  return (
    <div>
      <dt className="text-3xl tracking-tight" style={SERIF}>{big}</dt>
      <dd className="mt-1 text-xs leading-snug text-[#6b6358]">{text}</dd>
    </div>
  );
}

/** "Get your crew on it": sign-up for an organization. Preview only, nothing is sent. */
function SignupCard() {
  const [done, setDone] = useState<string | null>(null);
  const [form, setForm] = useState({ company: "", name: "", email: "", size: "11-50" });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <div id="signup" className="rise w-full max-w-[440px] [perspective:900px]" style={{ animationDelay: "0.9s" }}>
      <div className="rounded-2xl border border-white/70 bg-white/70 p-5 shadow-[0_20px_60px_-20px_rgba(60,40,20,.35)] backdrop-blur-xl">
        {done ? (
          <div key="done" className="flip-in py-2">
            <p className="text-2xl tracking-tight" style={SERIF}>You&apos;re on the list.</p>
            <p className="mt-1 text-sm text-[#5d564d]">Thanks, {done}. We&apos;ll reach out to set up your first crew.</p>
            <button onClick={() => setDone(null)} className="mt-3 text-xs font-semibold text-[#e8663d]">Sign up another team</button>
          </div>
        ) : (
          <form key="form" onSubmit={(e) => { e.preventDefault(); setDone(form.name.split(" ")[0] || form.company); }} className="flex flex-col gap-2.5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#8a8174]">Get your crew on it</p>
            <div className="grid grid-cols-2 gap-2">
              <input required value={form.company} onChange={set("company")} placeholder="Company" className="rounded-xl border border-[#e4d8c6] bg-white/80 px-3 py-2.5 text-sm outline-none focus:border-[#e8663d]" />
              <input required value={form.name} onChange={set("name")} placeholder="Your name" className="rounded-xl border border-[#e4d8c6] bg-white/80 px-3 py-2.5 text-sm outline-none focus:border-[#e8663d]" />
            </div>
            <div className="grid grid-cols-[1fr_auto] gap-2">
              <input required type="email" value={form.email} onChange={set("email")} placeholder="Work email" className="rounded-xl border border-[#e4d8c6] bg-white/80 px-3 py-2.5 text-sm outline-none focus:border-[#e8663d]" />
              <select value={form.size} onChange={set("size")} className="rounded-xl border border-[#e4d8c6] bg-white/80 px-3 py-2.5 text-sm outline-none focus:border-[#e8663d]" aria-label="Crew size">
                <option value="1-10">1-10 workers</option>
                <option value="11-50">11-50 workers</option>
                <option value="51-200">51-200 workers</option>
                <option value="200+">200+ workers</option>
              </select>
            </div>
            <button className="mt-1 flex items-center justify-center gap-2 rounded-full bg-[#1d1b19] px-4 py-2.5 text-sm font-semibold text-white hover:opacity-90">
              Request access <span aria-hidden>→</span>
            </button>
            <p className="text-center text-[11px] text-[#9a9083]">Preview: nothing is sent yet.</p>
          </form>
        )}
      </div>
    </div>
  );
}
