import { useEffect, useMemo, useRef } from "react";
import { agentsIn, clock, eventTone, isReply, mentionTarget, speaker, threadFor } from "../lib";
import { useStore } from "../store";
import { EventBody } from "./EventBody";

const NO_WORKERS: never[] = [];

/** One agent's conversation: what was said to it (left) and what it said or posted (right). */
export function Threads() {
  const events = useStore((s) => s.events);
  const workers = useStore((s) => s.status?.workers ?? NO_WORKERS);
  const selected = useStore((s) => s.threadAgent);
  const openThread = useStore((s) => s.openThread);
  const setTarget = useStore((s) => s.setMentionTarget);
  const endRef = useRef<HTMLDivElement>(null);
  const agents = useMemo(() => agentsIn(events, workers.map((w) => w.name)), [events, workers]);
  const current = selected ?? agents.find((a) => a.count > 0)?.name ?? agents[0]?.name ?? null;
  const thread = current ? threadFor(events, current) : [];
  const worker = workers.find((w) => w.name === current);

  useEffect(() => { endRef.current?.scrollIntoView?.({ block: "end" }); }, [thread.length, current]);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[11rem_1fr]">
      <ul aria-label="agents" className="overflow-y-auto border-r p-2 text-sm" style={{ borderColor: "var(--line)", maxHeight: "70vh" }}>
        {agents.map((a) => (
          <li key={a.name}>
            <button type="button" aria-pressed={a.name === current} onClick={() => openThread(a.name)} className="mono flex w-full items-center justify-between rounded px-2 py-1 text-left"
              style={{ backgroundColor: a.name === current ? "var(--line)" : "transparent" }}>
              <span className="truncate">{a.name}</span><span className="text-xs tone-muted">{a.count}</span>
            </button>
          </li>
        ))}
        {agents.length === 0 && <li className="tone-muted p-2">No agents yet.</li>}
      </ul>
      <div className="flex min-w-0 flex-col">
        <div className="flex items-center justify-between gap-2 border-b p-2 text-sm" style={{ borderColor: "var(--line)" }}>
          <span className="mono font-semibold">{current ?? "—"}</span>
          {worker && <button type="button" className="rounded border px-2 py-0.5 text-xs" style={{ borderColor: "var(--line)" }} onClick={() => setTarget(worker.name)}>@ message {worker.name}</button>}
        </div>
        <ol aria-label="thread" className="flex-1 space-y-2 overflow-y-auto p-3 text-sm" style={{ maxHeight: "70vh" }}>
          {thread.length === 0 && <li className="tone-muted">Nothing said to or by {current ?? "this agent"} yet.</li>}
          {thread.map((e) => {
            const mine = speaker(e) === current;
            const to = mentionTarget(e);
            return (
              <li key={e.seq} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
                <div className="max-w-[85%] rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", backgroundColor: mine ? "var(--bg)" : "transparent" }}>
                  <div className="mono mb-1 flex flex-wrap gap-2 text-xs tone-muted">
                    <span>{clock(e.ts)}</span>
                    <span className="font-medium">{mine ? current : speaker(e)}{to && to !== current ? ` → ${to}` : to && !mine ? ` → ${to}` : ""}</span>
                    <span className={`font-semibold uppercase tone-${eventTone(e)}`}>{isReply(e) ? "reply" : e.kind}</span>
                  </div>
                  <EventBody body={e.body} />
                </div>
              </li>
            );
          })}
          <div ref={endRef} />
        </ol>
      </div>
    </div>
  );
}
