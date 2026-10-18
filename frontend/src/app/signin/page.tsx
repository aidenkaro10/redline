"use client";

/**
 * Manager sign-in, reached from "Sign in" on the selling page. Same warm look as that page.
 * Pick one of the two saved crews: the demo crew (open) or the friends crew (passcode).
 * The passcode is checked on the server (/api/signin), against Render's private settings.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Logo, SERIF } from "@/components/landing/Hero";
import { useTeams } from "@/lib/crew";
import { DEMO_EMAIL, DEMO_SESSION, signIn } from "@/lib/session";

export default function SignInPage() {
  const router = useRouter();
  const teams = useTeams();
  const [email, setEmail] = useState("");
  const [team, setTeam] = useState("demo");
  const [passcode, setPasscode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = teams.find((t) => t.id === team) ?? teams[0];

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/signin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ team, passcode }) });
      const res = await r.json() as { ok: boolean; message?: string; team?: { id: string; name: string } };
      if (!res.ok || !res.team) { setError(res.message ?? "Couldn't sign in. Try again."); return; }
      signIn({ email: email.trim() || DEMO_EMAIL, team: res.team.id, teamName: res.team.name });
      router.push("/manager");
    } catch {
      setError("Couldn't reach the server. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="grid min-h-screen place-items-center bg-[#f4ede2] px-6 py-12 text-[#1d1b19]">
      <div className="w-full max-w-[400px]">
        <Link href="/preview" className="mb-10 flex items-center gap-2.5 text-[15px] font-semibold tracking-tight">
          <Logo /> Redline
        </Link>
        <h1 className="text-[40px] leading-[1.02] tracking-[-0.025em]" style={SERIF}>Sign in to your crew.</h1>
        <p className="mt-3 text-[15px] leading-relaxed text-[#5d564d]">See who&apos;s fine, who needs a break, and every site&apos;s risk for tomorrow.</p>

        <form className="mt-8 flex flex-col gap-3" onSubmit={submit}>
          <label htmlFor="signin-email" className="text-[13px] font-medium text-[#5d564d]">Work email</label>
          <input id="signin-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com" className={INPUT} />

          <p className="mt-2 text-[13px] font-medium text-[#5d564d]" id="crew-label">Crew</p>
          <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-labelledby="crew-label">
            {teams.map((t) => (
              <button key={t.id} type="button" role="radio" aria-checked={team === t.id}
                onClick={() => { setTeam(t.id); setError(null); }}
                className={`rounded-xl border px-3.5 py-3 text-left transition-colors ${team === t.id ? "border-[#1d1b19] bg-white" : "border-black/10 bg-white/50 hover:bg-white"}`}>
                <span className="block text-[15px] font-semibold">{t.name}</span>
                <span className="block text-[12px] text-[#8a8174]">{t.locked ? "Passcode" : "Open to anyone"}</span>
              </button>
            ))}
          </div>

          {chosen?.locked && (
            <>
              <label htmlFor="signin-passcode" className="mt-2 text-[13px] font-medium text-[#5d564d]">Passcode</label>
              <input id="signin-passcode" type="password" required autoComplete="current-password" value={passcode}
                onChange={(e) => setPasscode(e.target.value)} placeholder="Ask Aiden" className={INPUT} />
            </>
          )}

          {error && <p className="text-[13px] font-medium text-[#b42318]" role="alert">{error}</p>}

          <button disabled={busy} className="mt-2 rounded-full bg-[#1d1b19] px-5 py-3 text-[15px] font-semibold text-white transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] hover:scale-[1.01] active:scale-[0.98] disabled:opacity-60">
            {busy ? "Signing in…" : `Open ${chosen?.name ?? "crew"}`}
          </button>
        </form>

        <div className="my-6 flex items-center gap-3 text-[12px] text-[#8a8174]">
          <span className="h-px flex-1 bg-black/10" /> or <span className="h-px flex-1 bg-black/10" />
        </div>
        <button onClick={() => { signIn(DEMO_SESSION); router.push("/manager"); }}
          className="w-full rounded-full border border-black/10 bg-white/60 px-5 py-3 text-[15px] font-semibold transition-colors hover:bg-white">
          Use the demo crew
        </button>
        <p className="mt-6 text-center text-[12px] text-[#8a8174]">Preview: your email just labels the dashboard and stays in this browser.</p>
      </div>
    </main>
  );
}

const INPUT = "rounded-xl border border-black/10 bg-white px-4 py-3 text-[16px] outline-none transition-shadow focus:border-[#1d1b19]/40 focus:shadow-[0_0_0_4px_rgba(29,27,25,0.06)]";
