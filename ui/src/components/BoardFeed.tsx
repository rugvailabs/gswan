import { useEffect, useRef, useState } from "react";
import { clock, eventTone, filterEvents, isReply, mentionTarget, speaker } from "../lib";
import { EventBody } from "./EventBody";
import { Threads } from "./Threads";
import { useStore } from "../store";
import { EVENT_KINDS } from "../types";

export function BoardFeed() {
  const events = useStore((s) => s.events);
  const kinds = useStore((s) => s.kinds);
  const agent = useStore((s) => s.agent);
  const toggleKind = useStore((s) => s.toggleKind);
  const setAgent = useStore((s) => s.setAgent);
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const [follow, setFollow] = useState(true);
  const endRef = useRef<HTMLDivElement>(null);
  const shown = filterEvents(events, kinds, agent);
  const agents = [...new Set(events.map((e) => e.agent))].sort();

  useEffect(() => {
    if (follow) endRef.current?.scrollIntoView?.({ block: "end" });
  }, [shown.length, follow]);

  return (
    <section aria-label="board" className="flex min-h-[24rem] flex-col rounded-lg border" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
      <div role="tablist" aria-label="view" className="flex gap-1 border-b p-2 text-sm" style={{ borderColor: "var(--line)" }}>
        {(["board", "threads"] as const).map((v) => (
          <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => setView(v)} className="rounded px-3 py-1" style={{ backgroundColor: view === v ? "var(--line)" : "transparent" }}>
            {v === "board" ? "Board (everything)" : "Conversations (per agent)"}
          </button>
        ))}
      </div>
      {view === "threads" ? <Threads /> : <>
      <div className="flex flex-wrap items-center gap-2 border-b p-3 text-xs" style={{ borderColor: "var(--line)" }}>
        {EVENT_KINDS.map((k) => (
          <button key={k} aria-pressed={kinds.has(k)} onClick={() => toggleKind(k)} className="rounded-full border px-2 py-0.5"
            style={{ borderColor: "var(--line)", backgroundColor: kinds.has(k) ? "var(--line)" : "transparent" }}>{k}</button>
        ))}
        <select aria-label="agent filter" value={agent ?? ""} onChange={(e) => setAgent(e.target.value || null)} className="ml-auto rounded border bg-transparent px-1 py-0.5" style={{ borderColor: "var(--line)" }}>
          <option value="">all agents</option>
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <label className="flex items-center gap-1"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
      </div>
      <ol className="flex-1 overflow-y-auto p-3 text-sm" style={{ maxHeight: "70vh" }}>
        {shown.length === 0 && <li className="tone-muted">No events{events.length ? " match the filters" : " yet"}.</li>}
        {shown.map((e) => (
          <li key={e.seq} className="grid grid-cols-[4.5rem_9rem_4.5rem_1fr] items-baseline gap-2 border-b py-1.5 last:border-0" style={{ borderColor: "var(--line)" }}>
            <span className="mono text-xs tone-muted">{clock(e.ts)}</span>
            <span className="mono truncate font-medium" title={`${speaker(e)}${mentionTarget(e) ? " → " + mentionTarget(e) : ""}`}>
              {speaker(e)}{mentionTarget(e) && <span className="tone-muted"> → {mentionTarget(e)}</span>}
            </span>
            <span className={`text-xs font-semibold uppercase tone-${eventTone(e)}`}>{isReply(e) ? "reply" : e.kind}</span>
            <EventBody body={e.body} />
          </li>
        ))}
        <div ref={endRef} />
      </ol>
      </>}
    </section>
  );
}
