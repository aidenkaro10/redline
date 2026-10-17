"use client";

/**
 * Planner: chat with the planning agent (session B) next to today's schedule.
 *
 * The agent suggests and explains. It never makes safety calls: every plan it
 * proposes goes through the rule checker (code), and the heat limits are fixed.
 */

import { useEffect, useRef, useState } from "react";

import { SchedulePanel } from "@/components/SchedulePanel";
import { Card } from "@/components/ui";
import type { Schedule } from "@/lib/contract";
import type { ChatReply } from "@/lib/contract-extra";
import { apiPost, useFetch, useSimNow } from "@/lib/live";

interface Msg { who: "you" | "agent"; text: string; tools?: string[]; llm?: boolean }

const SUGGESTIONS = [
  "Is today's plan safe?",
  "Storms are coming at 3. What should we change?",
  "Who on the crew runs hottest?",
  "What if we finish the line work after lunch?",
];

export default function PlannerPage() {
  const now = useSimNow(1000);
  const { data: schedule, error: schedError } = useFetch<Schedule>("/schedule", 15000);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [msgs, busy]);

  const send = async (text: string) => {
    const t = text.trim();
    if (!t || busy) return;
    setMsgs((m) => [...m, { who: "you", text: t }]);
    setInput("");
    setBusy(true);
    try {
      const r = await apiPost<ChatReply>("/planner/chat", { message: t, session_id: sessionId });
      if (!r || typeof r.reply !== "string") throw new Error("no reply");
      setSessionId(r.session_id);
      setMsgs((m) => [...m, { who: "agent", text: r.reply, tools: r.tool_calls?.map((c) => c.tool), llm: r.llm }]);
    } catch {
      setMsgs((m) => [...m, { who: "agent", text: "I couldn't reach the planner. Is session B's router running on this backend?" }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto grid max-w-[1400px] gap-4 px-4 py-6 lg:grid-cols-5">
      <Card className="lg:col-span-2" title="Ask the planner" right="AI suggests, the rule checker decides" bodyClass="flex flex-col p-0">
        <div className="flex max-h-[60vh] min-h-[360px] flex-1 flex-col gap-3 overflow-y-auto p-4">
          {msgs.length === 0 && (
            <div className="text-sm text-ink-2">
              <p>Ask about today&apos;s plan, the weather or the crew. Try:</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)} className="rounded-full border border-line px-3 py-1.5 text-left text-xs font-medium hover:bg-surface-2">{s}</button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={`flex flex-col ${m.who === "you" ? "items-end" : "items-start"}`}>
              <div className={`max-w-[90%] rounded-2xl px-3.5 py-2 text-sm ${m.who === "you" ? "bg-accent text-accent-ink" : "bg-surface-2 text-ink"}`}>{m.text}</div>
              {m.tools && m.tools.length > 0 && (
                <p className="mt-1 text-[11px] text-muted">Used: {m.tools.join(", ")}{m.llm === false ? " · AI off, optimizer only" : ""}</p>
              )}
            </div>
          ))}
          {busy && <p className="text-xs text-muted">Planner is thinking…</p>}
          <div ref={endRef} />
        </div>
        <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="flex gap-2 border-t border-line p-3">
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask a what-if…"
            className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm" />
          <button disabled={busy || !input.trim()} className="rounded-lg bg-accent px-4 py-2 text-sm font-bold text-accent-ink disabled:opacity-50">Send</button>
        </form>
      </Card>

      <Card className="lg:col-span-3" title="Today's schedule" right="Verified by the rule checker">
        {schedule ? <SchedulePanel schedule={schedule} now={now} />
          : <p className="text-sm text-muted">{schedError ? "No schedule from the planner yet." : "Loading…"}</p>}
        <p className="mt-4 text-xs text-muted">
          Safety limits are hard-coded and never learned. The AI never makes a safety call: it proposes plans, the rule checker accepts or blocks them.
        </p>
      </Card>
    </main>
  );
}
