/**
 * Hold-to-confirm (the phone's SOS button, like holding the band button 3 s).
 * Pure logic with an injectable clock so tests can check it without a browser.
 */
export interface Hold {
  start(): void;
  /** Call often while held. Returns progress 0..1, and fires once at 1. */
  tick(): number;
  /** Finger lifted. Returns true only if it had already fired. */
  release(): boolean;
  readonly fired: boolean;
}

export function createHold(holdMs: number, onFire: () => void, now: () => number = () => Date.now()): Hold {
  let startedAt: number | null = null;
  let fired = false;
  return {
    start() { startedAt = now(); fired = false; },
    tick() {
      if (startedAt === null) return 0;
      const p = Math.min(1, (now() - startedAt) / holdMs);
      if (p >= 1 && !fired) { fired = true; startedAt = null; onFire(); }
      return p;
    },
    release() { const f = fired; startedAt = null; return f; },
    get fired() { return fired; },
  };
}
