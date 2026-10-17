/**
 * The MOCK backend's HTTP side. Only used when NEXT_PUBLIC_BACKEND_URL is empty.
 *
 *   GET  /api/stream?view=crew|worker|demo[&worker_id=...]   live events (Server-Sent Events)
 *   GET  /api/...    and   POST /api/...                      REST calls (see the contract doc)
 *
 * The real backend (session A) sends the same events over a WebSocket at /ws/live.
 */

import type { ViewFilter } from "@/lib/contract.ts";
import { getEngine } from "@/mock/server.ts";
import { RESULTS } from "@/mock/extras.ts";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: Request, ctx: Ctx) {
  const { path } = await ctx.params;
  const url = new URL(req.url);
  const engine = getEngine();

  if (path[0] === "stream") return stream(req, {
    view: (url.searchParams.get("view") as ViewFilter["view"]) || "crew",
    worker_id: url.searchParams.get("worker_id") || undefined,
  });

  if (path[0] === "results") return Response.json(RESULTS);

  const res = engine.handle("GET", path, url.searchParams, {});
  return Response.json(res.json, { status: res.status });
}

export async function POST(req: Request, ctx: Ctx) {
  const { path } = await ctx.params;
  const url = new URL(req.url);
  let body: Record<string, unknown> = {};
  try { body = await req.json(); } catch { /* empty body is fine */ }
  const res = getEngine().handle("POST", path, url.searchParams, body);
  return Response.json(res.json, { status: res.status });
}

/** Keep the connection open and write each event as one "data:" line. */
function stream(req: Request, filter: ViewFilter) {
  const engine = getEngine();
  const enc = new TextEncoder();
  let unsubscribe = () => {};
  const body = new ReadableStream({
    start(controller) {
      const send = (ev: unknown) => controller.enqueue(enc.encode(`data: ${JSON.stringify(ev)}\n\n`));
      const close = () => { try { controller.close(); } catch { /* already closed */ } };
      unsubscribe = engine.subscribe(filter, send, close);
      req.signal.addEventListener("abort", () => {
        unsubscribe();
        try { controller.close(); } catch { /* already closed */ }
      });
    },
    cancel() { unsubscribe(); },
  });
  return new Response(body, {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", Connection: "keep-alive" },
  });
}
