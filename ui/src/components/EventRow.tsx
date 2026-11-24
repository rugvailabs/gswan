import { clock, eventTone, isReply, mentionTarget, speaker } from "../lib";
import type { BoardEvent } from "../types";
import { EventBody } from "./EventBody";

/** One board event: time, who (and to whom), kind, body. `current` marks the replay cursor. */
export function EventRow({ e, current = false }: { e: BoardEvent; current?: boolean }) {
  const to = mentionTarget(e);
  return (
    <li aria-current={current ? "step" : undefined} className="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-baseline gap-x-2 gap-y-0.5 border-b py-1.5 last:border-0 sm:grid-cols-[4.5rem_9rem_4.5rem_minmax(0,1fr)]"
      style={{ borderColor: "var(--line)", ...(current ? { backgroundColor: "var(--bg)", boxShadow: "inset 3px 0 0 var(--info)" } : {}) }}>
      <span className="mono text-xs tone-muted">{clock(e.ts)}</span>
      <span className="mono truncate font-medium" title={`${speaker(e)}${to ? " → " + to : ""}`}>
        {speaker(e)}{to && <span className="tone-muted"> → {to}</span>}
      </span>
      <span className={`text-xs font-semibold uppercase tone-${eventTone(e)}`}>{isReply(e) ? "reply" : e.kind}</span>
      <span className="col-span-3 min-w-0 sm:col-span-1"><EventBody body={e.body} /></span>
    </li>
  );
}
