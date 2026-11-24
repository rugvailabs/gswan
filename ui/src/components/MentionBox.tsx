import { useEffect, useState, type FormEvent } from "react";
import { useStore } from "../store";

const NO_WORKERS: never[] = [];

function groupsOf(workers: { name: string; group?: string | null }[]): [string, string[]][] {
  const m = new Map<string, string[]>();
  for (const w of workers) if (w.group) m.set(w.group, [...(m.get(w.group) ?? []), w.name]);
  return [...m.entries()];
}

export function MentionBox() {
  const status = useStore((s) => s.status);
  const workers = status?.workers ?? NO_WORKERS;  // selector must return a stable reference (a fresh [] loops forever)
  const target = useStore((s) => s.mentionTarget);
  const setTarget = useStore((s) => s.setMentionTarget);
  const sendMention = useStore((s) => s.sendMention);
  const sendNote = useStore((s) => s.sendNote);
  const [mode, setMode] = useState<"mention" | "note" | "worker" | "budget">("mention");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const worker = target ?? workers[0]?.name ?? "";

  useEffect(() => { if (!target && workers[0]) setTarget(workers[0].name); }, [target, workers, setTarget]);

  async function submit(ev?: FormEvent) {
    ev?.preventDefault();
    const message = text.trim();
    if (!message || busy || (mode === "mention" && !worker)) return;
    setBusy(true);
    try {
      if (mode === "mention") await sendMention(worker, message);
      else await sendNote(message);
      setText("");
    } catch {
      /* error is shown by the store; keep the draft */
    } finally {
      setBusy(false);
    }
  }

  if (mode === "worker" || mode === "budget") {
    return (
      <div className="flex flex-col gap-2 rounded-lg border p-3 self-start" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
        <ModeTabs mode={mode} setMode={setMode} />
        {mode === "worker" ? <AddWorker /> : <Budget />}
      </div>
    );
  }

  return (
    <form onSubmit={submit} aria-label="send" className="flex flex-col gap-2 rounded-lg border p-3 self-start" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
      <ModeTabs mode={mode} setMode={setMode} />
      {mode === "mention" && (
        <label className="flex flex-col gap-1 text-xs tone-muted">Worker
          <select value={worker} onChange={(e) => setTarget(e.target.value)} className="rounded border bg-transparent px-2 py-1 text-sm" style={{ borderColor: "var(--line)", color: "var(--text)" }}>
            {workers.length === 0 && <option value="">no workers</option>}
            {workers.map((w) => <option key={w.name} value={w.name}>@{w.name}</option>)}
            {groupsOf(workers).map(([g, names]) => <option key={`g-${g}`} value={g}>@{g} (group: {names.join(", ")})</option>)}
            {workers.length > 1 && <option value="all">@all (every worker)</option>}
          </select>
        </label>
      )}
      <label className="flex flex-col gap-1 text-xs tone-muted">{mode === "mention" ? "Message" : "Note"}
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} maxLength={8000}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit(); }}
          className="rounded border bg-transparent p-2 text-sm" style={{ borderColor: "var(--line)", color: "var(--text)" }}
          placeholder={mode === "mention" ? "Wakes the worker's session. Costs tokens only now." : "Visible to every agent on the board."} />
      </label>
      <button type="submit" disabled={busy || !text.trim() || (mode === "mention" && !worker)} className="rounded px-3 py-1.5 text-sm font-medium disabled:opacity-50" style={{ backgroundColor: "var(--info)", color: "#fff" }}>
        {busy ? "Sending…" : mode === "mention" ? `Send to @${worker || "…"}` : "Post note"} <span className="text-xs opacity-70">⌘↵</span>
      </button>
    </form>
  );
}

type Mode = "mention" | "note" | "worker" | "budget";
const MODE_LABEL: Record<Mode, string> = { mention: "@ Mention", note: "Note", worker: "+ Worker", budget: "Budget" };

function ModeTabs({ mode, setMode }: { mode: Mode; setMode: (m: Mode) => void }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 text-sm">
      {(Object.keys(MODE_LABEL) as Mode[]).map((m) => (
        <button type="button" role="tab" key={m} aria-selected={mode === m} onClick={() => setMode(m)} className="rounded px-2 py-1" style={{ backgroundColor: mode === m ? "var(--line)" : "transparent" }}>
          {MODE_LABEL[m]}
        </button>
      ))}
    </div>
  );
}

const field = "rounded border bg-transparent px-2 py-1 text-sm";
const fieldStyle = { borderColor: "var(--line)", color: "var(--text)" } as const;
const list = (v: string) => v.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

function AddWorker() {
  const addWorker = useStore((s) => s.addWorker);
  const [f, setF] = useState({ name: "", goal: "", owns: "", tools: "", model: "sonnet" as "sonnet" | "haiku" | "opus", message: "" });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const ok = /^[a-z][a-z0-9-]{0,31}$/.test(f.name) && f.goal.trim() && list(f.owns).length > 0;
  async function submit(ev: FormEvent) {
    ev.preventDefault();
    if (!ok || busy) return;
    setBusy(true);
    try {
      await addWorker({ name: f.name, goal: f.goal.trim(), owns: list(f.owns), tools: list(f.tools), model: f.model, message: f.message.trim() || undefined });
      setDone(f.name);
      setF({ ...f, name: "", goal: "", owns: "", message: "" });
    } catch { /* shown by the store */ } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} aria-label="add worker" className="flex flex-col gap-2">
      <label className="flex flex-col gap-1 text-xs tone-muted">Name (lowercase, dashes)
        <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} className={`${field} mono`} style={fieldStyle} placeholder="docs-writer" /></label>
      <label className="flex flex-col gap-1 text-xs tone-muted">Goal
        <textarea value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} rows={3} className={field} style={fieldStyle} /></label>
      <label className="flex flex-col gap-1 text-xs tone-muted">Owns (paths it may write, comma or newline separated; must not overlap another worker)
        <textarea value={f.owns} onChange={(e) => setF({ ...f, owns: e.target.value })} rows={2} className={`${field} mono`} style={fieldStyle} placeholder="src/docs, README.md" /></label>
      <label className="flex flex-col gap-1 text-xs tone-muted">Extra tools (optional, e.g. Bash(npm run:*))
        <input value={f.tools} onChange={(e) => setF({ ...f, tools: e.target.value })} className={`${field} mono`} style={fieldStyle} /></label>
      <label className="flex flex-col gap-1 text-xs tone-muted">Model
        <select value={f.model} onChange={(e) => setF({ ...f, model: e.target.value as "sonnet" | "haiku" | "opus" })} className={field} style={fieldStyle}>
          <option value="sonnet">sonnet</option><option value="haiku">haiku (cheap)</option><option value="opus">opus</option></select></label>
      <label className="flex flex-col gap-1 text-xs tone-muted">First message (optional, wakes it now and costs tokens)
        <textarea value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} rows={2} className={field} style={fieldStyle} /></label>
      <button type="submit" disabled={!ok || busy} className="rounded px-3 py-1.5 text-sm font-medium disabled:opacity-50" style={{ backgroundColor: "var(--info)", color: "#fff" }}>{busy ? "Adding…" : "Add worker"}</button>
      {done && <p role="status" className="text-xs tone-ok">Added @{done}. If it says the worker limit was reached, raise it in the Budget tab.</p>}
    </form>
  );
}

function Budget() {
  const status = useStore((s) => s.status);
  const setLimits = useStore((s) => s.setLimits);
  const lim = status?.limits;
  const [v, setV] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  if (!lim) return <p className="text-sm tone-muted">No active swarm.</p>;
  const rows: [string, string, number][] = [["maxWorkers", "Max workers", lim.max_workers], ["maxWakeups", "Max wake-ups (turns)", lim.max_wakeups], ["maxWallS", "Max wall time (s)", lim.max_wall_s], ["maxTokens", "Max tokens (input + output)", lim.max_tokens]];
  async function save(ev: FormEvent) {
    ev.preventDefault();
    const body: Record<string, number> = {};
    for (const [k] of rows) if (v[k]?.trim() && Number(v[k]) > 0) body[k] = Number(v[k]);
    if (Object.keys(body).length === 0 || busy) return;
    setBusy(true);
    try { await setLimits(body); setV({}); } catch { /* shown by the store */ } finally { setBusy(false); }
  }
  return (
    <form onSubmit={save} aria-label="budget" className="flex flex-col gap-2">
      <p className="text-xs tone-muted">Only you can raise these; the coordinator cannot. Leave a field empty to keep it.</p>
      {rows.map(([k, label, cur]) => (
        <label key={k} className="flex flex-col gap-1 text-xs tone-muted">{label} (now {cur})
          <input type="number" min={1} step="any" value={v[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value })} className={`${field} mono`} style={fieldStyle} /></label>
      ))}
      <button type="submit" disabled={busy} className="rounded px-3 py-1.5 text-sm font-medium disabled:opacity-50" style={{ backgroundColor: "var(--info)", color: "#fff" }}>{busy ? "Saving…" : "Save limits"}</button>
    </form>
  );
}
