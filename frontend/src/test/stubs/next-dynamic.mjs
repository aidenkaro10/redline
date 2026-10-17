// next/dynamic stand-in: renders the loading placeholder (the real map needs a browser).
export default function dynamic(_loader, opts = {}) {
  return function Dynamic() { return opts.loading ? opts.loading() : null; };
}
