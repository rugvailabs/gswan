import { useMemo, useState } from "react";
import { aboutPart, ASK_STATUS, buildersOf, explainEvent, goalsFromEvents, ownedChip, partName, workerStory } from "../../plain";
import { useStore } from "../../store";
import type { WorkerView } from "../../types";
import { Bar, inputCls, inputStyle, primaryBtn, primaryStyle, secondaryBtn, secondaryStyle, Section, toneClass, toneVar } from "./ui";

/** One friendly card per part of the work (per builder). */
export function Parts() {
  const workers = useStore((s) => s.status?.workers);
  const events = useStore((s) => s.events);
  const goals = useMemo(() => goalsFromEvents(events), [events]);
  const builders = buildersOf(workers ?? []);
  return (
    <Section label="parts" title={`The parts${builders.length ? ` (${builders.length})` : ""}`}>
      {builders.length === 0 ? (
        <p className="text-sm tone-muted">No parts yet. The team lead is still working out how to split the work.</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {builders.map((w) => <PartCard key={w.name} w={w} goal={w.goal ?? goals.get(w.name) ?? ""} />)}
        </div>
      )}
    </Section>
  );
}

function PartCard({ w, goal }: { w: WorkerView; goal: string }) {
  const finding = useStore((s) => s.status?.monitor.find((f) => f.worker === w.name) ?? null);
  const events = useStore((s) => s.events);
  const workers = useStore((s) => s.status?.workers) ?? [w];
  const send = useStore((s) => s.sendMention);
  const story = workerStory(w, finding);
  const [open, setOpen] = useState(false);
  const [telling, setTelling] = useState(false);
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = partName(w.name);
  const mine = open ? events.filter((e) => aboutPart(e, w.name)).slice(-12).reverse() : [];

  const say = async (message: string, done: string) => {
    setBusy(true);
    try { await send(w.name, message); setNote(done); setText(""); setTelling(false); } catch { /* shown by the store */ } finally { setBusy(false); }
  };

  return (
    <article aria-label={`part ${name}`} className="flex flex-col gap-2 rounded-lg border p-3" style={{ borderColor: story.needsYou ? "var(--bad)" : "var(--line)", backgroundColor: "var(--bg)" }}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex flex-col items-start gap-1 text-left">
        <span className="text-base font-semibold">{name}</span>
        {goal && <span className="line-clamp-2 text-sm tone-muted">{goal}</span>}
      </button>
      <p className={`text-sm font-medium ${toneClass(story.tone)}`} aria-label={`${name} status`}>{story.text}</p>
      <Bar value={story.progress} label={`${name} progress`} color={toneVar(story.tone === "info" ? "info" : story.tone)} />
      {w.owns.length > 0 && (
        <ul className="flex flex-wrap gap-1" aria-label={`${name} works on`}>
          {w.owns.map((p) => {
            const c = ownedChip(p);
            return (
              <li key={p} className="mono rounded-full border px-2 py-0.5 text-xs" style={{ borderColor: "var(--line)" }} title={c.folder ? "a folder" : "a file"}>
                <span aria-hidden>{c.folder ? "▸ " : "· "}</span>{c.label}
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy} className={secondaryBtn} style={secondaryStyle}
          onClick={() => void say(ASK_STATUS, "Asked. Its answer will show up in the story.")}>What are you doing?</button>
        <button type="button" className={secondaryBtn} style={secondaryStyle} aria-expanded={telling} onClick={() => setTelling(!telling)}>Tell it something</button>
      </div>
      {telling && (
        <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void say(text.trim(), "Sent."); }}>
          <label className="text-xs tone-muted" htmlFor={`tell-${w.name}`}>Message for {name}</label>
          <input id={`tell-${w.name}`} value={text} onChange={(e) => setText(e.target.value)} className={inputCls} style={inputStyle} placeholder="For example: use blue buttons" />
          <button type="submit" disabled={busy || !text.trim()} className={primaryBtn} style={primaryStyle}>{busy ? "Sending…" : "Send"}</button>
        </form>
      )}
      {note && <p role="status" className="text-xs tone-ok">{note}</p>}
      {open && (
        <ul aria-label={`${name} story`} className="flex flex-col gap-1 border-t pt-2 text-sm" style={{ borderColor: "var(--line)" }}>
          {mine.length === 0 && <li className="tone-muted">Nothing has happened yet.</li>}
          {mine.map((e) => {
            const p = explainEvent(e, workers);
            return <li key={e.seq} className="flex gap-2"><span aria-hidden className={toneClass(p.tone)}>{p.icon}</span><span>{p.text}</span></li>;
          })}
        </ul>
      )}
    </article>
  );
}
