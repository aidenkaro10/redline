// next/navigation stand-in. Tests set the page with setRoute().
let route = { path: "/", search: "", params: {} };
export const router = { calls: [], replace(u) { this.calls.push(["replace", u]); }, push(u) { this.calls.push(["push", u]); } };
export function setRoute(r) { route = { path: "/", search: "", params: {}, ...r }; router.calls = []; }
export function usePathname() { return route.path; }
export function useSearchParams() { return new URLSearchParams(route.search); }
export function useParams() { return route.params; }
export function useRouter() { return router; }
