"use client";

/**
 * Manager sign-in, reached from "Sign in" on the selling page. Same warm look as that page.
 * Demo only: no password, the email is just remembered in this browser (lib/session.ts).
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Logo, SERIF } from "@/components/landing/Hero";
import { DEMO_EMAIL, signIn } from "@/lib/session";

export default function SignInPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");

  const go = (who: string) => { signIn(who); router.push("/manager"); };

  return (
    <main className="grid min-h-screen place-items-center bg-[#f4ede2] px-6 py-12 text-[#1d1b19]">
      <div className="w-full max-w-[400px]">
        <Link href="/preview" className="mb-12 flex items-center gap-2.5 text-[15px] font-semibold tracking-tight">
          <Logo /> Redline
        </Link>
        <h1 className="text-[40px] leading-[1.02] tracking-[-0.025em]" style={SERIF}>Sign in to your crew.</h1>
        <p className="mt-3 text-[15px] leading-relaxed text-[#5d564d]">See who&apos;s fine, who needs a break, and every site&apos;s risk for tomorrow.</p>

        <form className="mt-8 flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (email.trim()) go(email.trim()); }}>
          <label htmlFor="signin-email" className="text-[13px] font-medium text-[#5d564d]">Work email</label>
          <input id="signin-email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            className="rounded-xl border border-black/10 bg-white px-4 py-3 text-[16px] outline-none transition-shadow focus:border-[#1d1b19]/40 focus:shadow-[0_0_0_4px_rgba(29,27,25,0.06)]" />
          <button className="mt-1 rounded-full bg-[#1d1b19] px-5 py-3 text-[15px] font-semibold text-white transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] hover:scale-[1.01] active:scale-[0.98]">
            Continue
          </button>
        </form>

        <div className="my-6 flex items-center gap-3 text-[12px] text-[#8a8174]">
          <span className="h-px flex-1 bg-black/10" /> or <span className="h-px flex-1 bg-black/10" />
        </div>
        <button onClick={() => go(DEMO_EMAIL)}
          className="w-full rounded-full border border-black/10 bg-white/60 px-5 py-3 text-[15px] font-semibold transition-colors hover:bg-white">
          Use the demo crew
        </button>
        <p className="mt-6 text-center text-[12px] text-[#8a8174]">Preview: no password yet. Your email stays in this browser.</p>
      </div>
    </main>
  );
}
