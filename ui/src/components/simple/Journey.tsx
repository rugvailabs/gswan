import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { explainEvent, journey, stageOf, type Stage, type StageKey } from "../../plain";
import { useStore } from "../../store";
import { panel, toneClass } from "./ui";

const STATE_WORD: Record<Stage["state"], string> = { done: "done", active: "happening now", upcoming: "still ahead", skipped: "not needed" };

/** The seven steps of a run as a stepper. Arrow keys move between steps; Enter/Space or a click opens one. */
export function Journey() {
  const status = useStore((s) => s.status);
  const events = useStore((s) => s.events);
  const result = useStore((s) => s.result);
  const stages = useMemo(() => journey(status, events, result), [status, events, result]);
  const [open, setOpen] = useState<StageKey | null>(null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? i + 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? stages.length - 1 : null;
    if (next === null) return;
    e.preventDefault();
    refs.current[(next + stages.length) % stages.length]?.focus();
  };
  const chosen = stages.find((s) => s.key === open) ?? null;
  const happened = chosen ? events.filter((e) => stageOf(e) === chosen.key).slice(-15).reverse() : [];

  return (
    <section aria-label="progress steps" className="flex flex-col gap-3 rounded-xl border p-4" style={panel}>
      <h2 className="text-base font-semibold">How it is going</h2>
      <ol className="flex gap-1 overflow-x-auto pb-1" aria-label="steps">
        {stages.map((s, i) => {
          const color = s.state === "done" ? "var(--ok)" : s.state === "active" ? "var(--info)" : "var(--line)";
          return (
            <li key={s.key} className="flex min-w-[5.5rem] flex-1 flex-col items-center">
              <button ref={(el) => { refs.current[i] = el; }} type="button" onClick={() => setOpen(open === s.key ? null : s.key)} onKeyDown={(e) => onKey(e, i)}
                aria-expanded={open === s.key} aria-current={s.state === "active" ? "step" : undefined}
                aria-label={`${s.title}: ${STATE_WORD[s.state]}`}
                className="flex w-full flex-col items-center gap-1 rounded-lg px-1 py-2 text-center focus-visible:outline-2"
                style={open === s.key ? { backgroundColor: "var(--bg)" } : undefined}>
                <span className="relative flex h-8 w-8 items-center justify-center rounded-full border-2 text-sm font-semibold"
                  style={{ borderColor: color, backgroundColor: s.state === "done" ? "var(--ok)" : "transparent", color: s.state === "done" ? "#fff" : s.state === "active" ? "var(--info)" : "var(--muted)" }}>
                  {s.state === "active" && <span aria-hidden className="absolute inset-0 rounded-full motion-safe:animate-ping" style={{ backgroundColor: "var(--info)", opacity: 0.25 }} />}
                  {s.state === "done" ? "✓" : s.state === "skipped" ? "–" : i + 1}
                </span>
                <span className={`text-xs font-medium ${s.state === "upcoming" || s.state === "skipped" ? "tone-muted" : ""}`}>{s.title}</span>
                <span className="text-[0.65rem] tone-muted">{STATE_WORD[s.state]}</span>
              </button>
            </li>
          );
        })}
      </ol>
      {chosen && (
        <div role="region" aria-label={`step ${chosen.title}`} className="rounded-lg border p-3 text-sm" style={{ borderColor: "var(--line)" }}>
          <p className="font-medium">{chosen.title} — {STATE_WORD[chosen.state]}</p>
          <p className="tone-muted">{chosen.blurb}</p>
          {happened.length > 0 ? (
            <ul className="mt-2 flex flex-col gap-1" aria-label={`what happened in ${chosen.title}`}>
              {happened.map((e) => {
                const p = explainEvent(e, status?.workers ?? []);
                return <li key={e.seq} className="flex gap-2"><span aria-hidden className={toneClass(p.tone)}>{p.icon}</span><span>{p.text}</span></li>;
              })}
            </ul>
          ) : (
            <p className="mt-2 text-xs tone-muted">Nothing has happened in this step yet.</p>
          )}
        </div>
      )}
    </section>
  );
}
