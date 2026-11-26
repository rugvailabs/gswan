import { useState } from "react";
import { clock } from "../../lib";
import { explainEvent } from "../../plain";
import { useStore } from "../../store";
import type { WorkerView } from "../../types";
import { Section, toneClass } from "./ui";

const PAGE = 60;
const NONE: WorkerView[] = [];

/** The live board, one plain sentence per event, newest first. */
export function Story() {
  const events = useStore((s) => s.events);
  const workers = useStore((s) => s.status?.workers) ?? NONE;
  const [tech, setTech] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const list = events.slice().reverse().slice(0, shown);
  return (
    <Section label="story" title="What is happening">
      <label className="flex items-center gap-2 text-xs tone-muted">
        <input type="checkbox" checked={tech} onChange={(e) => setTech(e.target.checked)} /> Show technical detail
      </label>
      {list.length === 0 ? (
        <p className="text-sm tone-muted">Nothing has happened yet.</p>
      ) : (
        <ol aria-label="story events" className="flex flex-col gap-2">
          {list.map((e) => {
            const p = explainEvent(e, workers);
            return (
              <li key={e.seq} className="flex gap-2 text-sm">
                <span aria-hidden className={`w-4 flex-none text-center ${toneClass(p.tone)}`}>{p.icon}</span>
                <div className="min-w-0 flex-1">
                  <p>{p.text}</p>
                  {tech && (
                    <p className="mono mt-0.5 break-words text-xs tone-muted">
                      {clock(e.ts)} · {e.agent} · {e.kind}: {p.detail}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {events.length > shown && (
        <button type="button" className="self-start text-sm tone-info underline" onClick={() => setShown(shown + PAGE)}>Show earlier</button>
      )}
    </Section>
  );
}
