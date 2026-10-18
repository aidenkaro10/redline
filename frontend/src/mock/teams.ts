/**
 * The two saved crews ("accounts") in the built-in demo backend.
 *
 *   demo     the 6 made-up workers plus Aiden. Anyone can open it ("Use the demo crew").
 *   friends  Aiden plus his friends. Opening it on the manager view needs a passcode.
 *
 * The GitHub repo is public, so real names and the passcode are NOT in this file.
 * They come from Render's private settings (Environment tab), read on the server only:
 *
 *   REDLINE_OWNER_NAME        Aiden's name as it shows in both crews     (default "Aiden")
 *   REDLINE_FRIENDS           friends' names, comma separated           e.g. "Sam, Priya, Luis"
 *   REDLINE_FRIENDS_TEAM      the friends crew's name                    (default "Friends crew")
 *   REDLINE_FRIENDS_PASSCODE  passcode for the friends crew on the manager view
 *
 * Locally (no Render settings), the friends crew uses placeholder names and the passcode "redline".
 */

export const DEMO_TEAM = "demo";
export const FRIENDS_TEAM = "friends";

export interface TeamInfo {
  id: string;
  name: string;
  locked: boolean;      // true = the manager view asks for a passcode
}

const env = (key: string) => (typeof process !== "undefined" ? process.env[key] : undefined)?.trim() || "";

export function ownerName() {
  return env("REDLINE_OWNER_NAME") || "Aiden";
}

export function friendNames(): string[] {
  const names = env("REDLINE_FRIENDS").split(",").map((s) => s.trim()).filter(Boolean);
  return names.length ? names.slice(0, 20) : ["Friend 1", "Friend 2", "Friend 3"];
}

export function teams(): TeamInfo[] {
  return [
    { id: DEMO_TEAM, name: "Demo crew", locked: false },
    { id: FRIENDS_TEAM, name: env("REDLINE_FRIENDS_TEAM") || "Friends crew", locked: true },
  ];
}

/** Can this passcode open this crew? On the hosted site the friends passcode must be set in Render. */
export function checkPasscode(team: string, passcode: string): { ok: true; team: TeamInfo } | { ok: false; message: string } {
  const t = teams().find((x) => x.id === team);
  if (!t) return { ok: false, message: "That crew doesn't exist." };
  if (!t.locked) return { ok: true, team: t };
  const real = env("REDLINE_FRIENDS_PASSCODE");
  const hosted = !!env("RENDER");
  if (!real && hosted) return { ok: false, message: "The friends crew isn't set up yet. Add REDLINE_FRIENDS_PASSCODE in Render." };
  const expected = real || "redline";               // local testing only
  return passcode.trim() === expected ? { ok: true, team: t } : { ok: false, message: "Wrong passcode. Try again." };
}
