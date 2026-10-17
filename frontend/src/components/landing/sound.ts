/**
 * Sound for the selling page (all made with ElevenLabs, files in public/landing/).
 * Everything is off until the visitor clicks "Play with sound" (browsers block autoplay anyway).
 */

let on = false;
const cache: Record<string, HTMLAudioElement> = {};

function get(name: string) {
  cache[name] ??= new Audio(`/landing/${name}.mp3`);
  return cache[name];
}

/** Turn sound on/off. On = the cicada heat loop plays quietly underneath everything. */
export function setSound(value: boolean) {
  on = value;
  const loop = get("heat-loop");
  loop.loop = true;
  loop.volume = 0.3;
  if (value) loop.play().catch(() => {});
  else loop.pause();
}

/** Play one effect (thunder, buzz, whoosh, clear). Does nothing while sound is off. */
export function play(name: string, volume = 0.8) {
  if (!on) return;
  const a = get(name).cloneNode() as HTMLAudioElement;   // a copy, so effects can overlap
  a.volume = volume;
  a.play().catch(() => {});
}

/** Fade the heat loop down during the storm (cicadas go quiet in the rain), back up after. */
export function duckLoop(down: boolean) {
  if (!on) return;
  get("heat-loop").volume = down ? 0.06 : 0.3;
}
