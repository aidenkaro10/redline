/**
 * Demo sign-in for the manager view. There's no account system yet, so "signing in"
 * just remembers the email in this browser. No password is asked for or stored.
 */

const KEY = "planit_manager";

export function signedInAs(): string | null {
  try { return localStorage.getItem(KEY); } catch { return null; }
}

export function signIn(email: string) {
  try { localStorage.setItem(KEY, email); } catch { /* private mode: still lets you in this visit */ }
}

export function signOut() {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}

export const DEMO_EMAIL = "lead@deerhaven-demo.com";
