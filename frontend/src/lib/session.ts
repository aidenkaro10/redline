/**
 * Manager sign-in, remembered in this browser. There's no account system yet: signing in
 * picks one of the two saved crews (demo or friends). The friends crew checks a passcode on
 * the server first (/api/signin); the passcode itself is never stored here.
 */

const KEY = "planit_manager";

export interface Session {
  email: string;
  team: string;        // "demo" or "friends"
  teamName: string;    // shown as the dashboard title
}

export const DEMO_EMAIL = "lead@deerhaven-demo.com";
export const DEMO_SESSION: Session = { email: DEMO_EMAIL, team: "demo", teamName: "Demo crew" };

export function signedIn(): Session | null {
  let raw: string | null = null;
  try { raw = localStorage.getItem(KEY); } catch { return null; }
  if (!raw) return null;
  try {
    const s = JSON.parse(raw) as Session;
    if (s && typeof s.team === "string") return s;
  } catch { /* an older sign-in that stored just the email */ }
  return { ...DEMO_SESSION, email: raw };
}

export function signIn(s: Session) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* private mode: still lets you in this visit */ }
}

export function signOut() {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}
