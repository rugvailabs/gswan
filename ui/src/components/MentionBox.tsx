import { useEffect, useState, type FormEvent } from "react";
import { useStore } from "../store";

const NO_WORKERS: never[] = [];

export function MentionBox() {
  const status = useStore((s) => s.status);
  const workers = status?.workers ?? NO_WORKERS;  // selector must return a stable reference (a fresh [] loops forever)
  const target = useStore((s) => s.mentionTarget);
  const setTarget = useStore((s) => s.setMentionTarget);
  const sendMention = useStore((s) => s.sendMention);
  const sendNote = useStore((s) => s.sendNote);
  const [mode, setMode] = useState<"mention" | "note">("mention");
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

  return (
    <form onSubmit={submit} aria-label="send" className="flex flex-col gap-2 rounded-lg border p-3 self-start" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
      <div role="tablist" className="flex gap-1 text-sm">
        {(["mention", "note"] as const).map((m) => (
          <button type="button" role="tab" key={m} aria-selected={mode === m} onClick={() => setMode(m)} className="rounded px-2 py-1" style={{ backgroundColor: mode === m ? "var(--line)" : "transparent" }}>
            {m === "mention" ? "@ Mention worker" : "Board note"}
          </button>
        ))}
      </div>
      {mode === "mention" && (
        <label className="flex flex-col gap-1 text-xs tone-muted">Worker
          <select value={worker} onChange={(e) => setTarget(e.target.value)} className="rounded border bg-transparent px-2 py-1 text-sm" style={{ borderColor: "var(--line)", color: "var(--text)" }}>
            {workers.length === 0 && <option value="">no workers</option>}
            {workers.map((w) => <option key={w.name} value={w.name}>@{w.name}</option>)}
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
