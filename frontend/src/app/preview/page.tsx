"use client";

/**
 * The selling page (laptop). The fun one; the phone apps (/worker, /) stay plain and serious.
 *
 *   Hero         lens intro, the live job site, hover workers, click for a storm
 *   BandBeat     the wristband + forecast chart, pinned and scrubbed by scroll
 *   CrewBeat     the crew lead's phone re-sorting worst first
 *   FloridaBeat  79 plant sites drop onto Florida + the organization sign-up
 *
 * Motion: GSAP timelines + ScrollTrigger, Lenis for smooth scrolling.
 * All pictures and the intro film were made with ElevenLabs (scripts/landing/eleven.py). No sound.
 * PREVIEW: the sign-up form doesn't send anything yet.
 */

import "lenis/dist/lenis.css";

import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";
import { useEffect, useRef, useState } from "react";

import { BandBeat, CrewBeat, FloridaBeat } from "@/components/landing/Beats";
import { Hero } from "@/components/landing/Hero";

export default function SellingPage() {
  const [night, setNight] = useState(false);
  const lenis = useRef<Lenis | null>(null);
  const signup = useRef<HTMLElement>(null);
  const introDone = useRef(false);             // the hero can finish (skip) its intro before this effect runs

  // Smooth scroll, driven by GSAP's clock so the scroll-linked timelines stay in sync
  useEffect(() => {
    gsap.registerPlugin(ScrollTrigger);
    history.scrollRestoration = "manual";        // a reload starts at the top, where the story starts
    window.scrollTo(0, 0);
    // Same buttery scroll as Aiden's other sites: 1.15 s glide with Lenis's soft expo ease
    const l = new Lenis({ duration: 1.15, smoothWheel: true, wheelMultiplier: 0.95 });
    if (!introDone.current) l.stop();             // no scrolling while the intro film plays
    l.on("scroll", ScrollTrigger.update);
    const tick = (time: number) => l.raf(time * 1000);
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);
    lenis.current = l;
    return () => { gsap.ticker.remove(tick); l.destroy(); };
  }, []);

  const toSignup = () => signup.current && lenis.current?.scrollTo(signup.current, { duration: 2.2 });

  return (
    <div className="bg-[#0d1220]">
      <Hero night={night} setNight={setNight} onSignup={toSignup}
        onIntroDone={() => { introDone.current = true; lenis.current?.start(); ScrollTrigger.refresh(); }} />
      <BandBeat />
      <CrewBeat />
      <FloridaBeat ref={signup} />
    </div>
  );
}
