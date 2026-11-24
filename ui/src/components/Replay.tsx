import { useEffect, useMemo, useRef, useState } from "react";
import { clock, duration, eventTone, isReply, nextKeyMoment, replayState } from "../lib";
import { useStore } from "../store";
import type { TaskStatus, WorkerView } from "../types";
import { EventRow } from "./EventRow";

const NO_WORKERS: WorkerView[] = [];
const TASK_TONE: Record<TaskStatus, string> = {
  open: "tone-muted", awaiting_verification: "tone-info", verifying: "tone-info", failed: "tone-bad", done: "tone-ok", escalated: "tone-bad",
};
const SPEEDS = [1, 4, 16] as const;
const STEP_MS = 800; // one event per step at 1x
const WINDOW = 40;   // events shown before the cursor

/** Step through the run event by event and see the swarm as it stood at that moment, rebuilt from the board. */
export function Replay() {
  const events = useStore((s) => s.events);
  const workers = useStore((s) => s.status?.workers ?? NO_WORKERS);
  const last = events.length - 1;
  const [index, setIndex] = useState(last);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(4);
  const currentRef = useRef<HTMLOListElement>(null);
  const i = Math.max(0, Math.min(index, last));
  const owners = useMemo(() => workers.filter((w) => w.task !== null).map((w) => w.name), [workers]);
  const state = useMemo(() => replayState(events, i, owners), [events, i, owners]);

  useEffect(() => {
    if (!playing) return;
    if (i >= last) { setPlaying(false); return; }
    const t = setTimeout(() => setIndex(i + 1), STEP_MS / speed);
    return () => clearTimeout(t);
  }, [playing, i, last, speed]);

  useEffect(() => {
    currentRef.current?.querySelector('[aria-current="step"]')?.scrollIntoView?.({ block: "nearest" });
  }, [i]);

  if (events.length === 0) return <p className="p-3 text-sm tone-muted">Nothing to replay yet: the board has no events.</p>;

  const go = (n: number) => { setPlaying(false); setIndex(Math.max(0, Math.min(n, last))); };
  const play = () => { if (i >= last) setIndex(0); setPlaying(true); };
  const prevKey = nextKeyMoment(events, i, -1);
  const nextKey = nextKeyMoment(events, i, 1);
  const btn = "rounded border px-2 py-1 text-xs disabled:opacity-40";
  const line = { borderColor: "var(--line)" };
  const shown = events.slice(Math.max(0, i - WINDOW + 1), i + 1);

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex flex-col gap-2 border-b p-3" style={line}>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={btn} style={line} disabled={i === 0} onClick={() => go(0)}>First</button>
          <button type="button" className={btn} style={line} disabled={prevKey < 0} onClick={() => go(prevKey)} title="Previous blocked, done, verdict, nudge or decision">◀ Key moment</button>
          <button type="button" className={btn} style={line} disabled={i === 0} onClick={() => go(i - 1)}>Back</button>
          <button type="button" className={`${btn} font-medium`} style={{ ...line, backgroundColor: "var(--line)" }} onClick={() => (playing ? setPlaying(false) : play())}>{playing ? "Pause" : i >= last ? "Play from start" : "Play"}</button>
          <button type="button" className={btn} style={line} disabled={i >= last} onClick={() => go(i + 1)}>Forward</button>
          <button type="button" className={btn} style={line} disabled={nextKey < 0} onClick={() => go(nextKey)} title="Next blocked, done, verdict, nudge or decision">Key moment ▶</button>
          <button type="button" className={btn} style={line} disabled={i >= last} onClick={() => go(last)}>Latest</button>
          <label className="ml-auto flex items-center gap-1 text-xs tone-muted">speed
            <select aria-label="speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value) as (typeof SPEEDS)[number])} className="rounded border bg-transparent px-1 py-0.5" style={line}>
              {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
            </select>
          </label>
        </div>
        <input type="range" aria-label="timeline" min={0} max={last} step={1} value={i} onChange={(e) => go(Number(e.target.value))} className="w-full" />
        <p aria-label="replay position" className="text-xs tone-muted">
          Event <span className="mono">{i + 1}</span> of <span className="mono">{events.length}</span>
          {state.at && <> · <span className="mono">{clock(state.at.ts)}</span> · {duration(state.elapsedS)} into the run</>}
          {" · "}{state.tasksDone}/{state.tasks} tasks done · <span className="mono">${state.costUsd.toFixed(3)}</span> reported so far
        </p>
      </div>
      <ul aria-label="state at this moment" className="grid gap-x-4 gap-y-1 border-b p-3 text-xs sm:grid-cols-2" style={line}>
        {state.agents.map((a) => (
          <li key={a.name} className="flex min-w-0 items-baseline gap-2">
            <span className="mono shrink-0 font-medium">{a.name}</span>
            {a.task && <span className={`shrink-0 font-medium ${TASK_TONE[a.task]}`}>{a.task.replace("_", " ")}</span>}
            {a.turns > 0 && <span className="mono shrink-0 tone-muted">{a.turns}t · ${a.costUsd.toFixed(3)}</span>}
            <span className="min-w-0 truncate tone-muted" title={a.last.body}>
              {a.events === 0 ? "mentioned, has not spoken yet" : <><span className={`tone-${eventTone(a.last)}`}>{isReply(a.last) ? "reply" : a.last.kind}</span> {a.last.body}</>}
            </span>
          </li>
        ))}
      </ul>
      <ol ref={currentRef} aria-label="replay events" className="flex-1 overflow-y-auto p-3 text-sm" style={{ maxHeight: "50vh" }}>
        {i - WINDOW + 1 > 0 && <li className="pb-1 text-xs tone-muted">{i - WINDOW + 1} earlier events not shown</li>}
        {shown.map((e) => <EventRow key={e.seq} e={e} current={e.seq === state.at?.seq} />)}
      </ol>
    </div>
  );
}
