/** Wrap an async action so a double tap only sends it once (until the first call finishes). */
export function singleFlight<A extends unknown[]>(fn: (...args: A) => Promise<unknown>) {
  let busy = false;
  return async (...args: A) => {
    if (busy) return;
    busy = true;
    try { await fn(...args); } finally { busy = false; }
  };
}
