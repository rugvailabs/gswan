import { useEffect, useRef, useState } from "react";
import { filterEvents } from "../lib";
import { EventRow } from "./EventRow";
import { Graph } from "./Graph";
import { Replay } from "./Replay";
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
        {(["board", "threads", "replay", "graph"] as const).map((v) => (
          <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => setView(v)} className="rounded px-3 py-1" style={{ backgroundColor: view === v ? "var(--line)" : "transparent" }}>
            {v === "board" ? "Board (everything)" : v === "threads" ? "Conversations (per agent)" : v === "replay" ? "Replay" : "Graph"}
          </button>
        ))}
      </div>
      {view === "threads" ? <Threads /> : view === "replay" ? <Replay /> : view === "graph" ? <Graph /> : <>
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
        {shown.map((e) => <EventRow key={e.seq} e={e} />)}
        <div ref={endRef} />
      </ol>
      </>}
    </section>
  );
}
