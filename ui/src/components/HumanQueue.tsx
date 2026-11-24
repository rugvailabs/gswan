import { useState } from "react";
import { budgetParts, raiseExhausted } from "../lib";
import { useStore } from "../store";
import type { Approval, ApprovalDecision } from "../types";

/** What needs a human right now: a paused swarm (budget used up) and commands workers asked to run. Hidden when empty. */
export function HumanQueue() {
  const status = useStore((s) => s.status);
  const pending = status?.pendingApprovals ?? [];
  const pause = status?.pause;
  if (!pending.length && !pause?.paused) return null;
  return (
    <section aria-label="needs you" className="flex flex-col gap-3 rounded-lg border p-4" style={{ backgroundColor: "var(--panel)", borderColor: "var(--warn)" }}>
      <h2 className="text-sm font-semibold">Needs you</h2>
      {pause?.paused && <PauseBanner />}
      {pending.map((a) => <ApprovalCard key={a.id} a={a} />)}
    </section>
  );
}

function PauseBanner() {
  const status = useStore((s) => s.status);
  const setLimits = useStore((s) => s.setLimits);
  const [busy, setBusy] = useState(false);
  const pause = status?.pause;
  const lift = raiseExhausted(status ?? undefined);
  const over = budgetParts(status ?? undefined).filter((p) => p.tone === "bad");
  const resume = async () => {
    setBusy(true);
    try { await setLimits(lift); } catch { /* shown by the store */ } finally { setBusy(false); }
  };
  return (
    <div role="alert" aria-label="paused" className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3" style={{ borderColor: "var(--line)" }}>
      <div className="min-w-0 text-sm">
        <p className="font-medium tone-warn">Paused: a budget is used up{over.length ? ` (${over.map((p) => `${p.label} ${p.text}`).join(", ")})` : ""}.</p>
        <p className="tone-muted">Waiting: {pause?.agents.join(", ") || "—"}. Nothing was lost; work continues where it stopped once you raise the budget.</p>
      </div>
      {Object.keys(lift).length > 0 && (
        <button type="button" disabled={busy} onClick={() => void resume()} className="rounded px-3 py-1.5 text-sm font-medium disabled:opacity-50"
          style={{ backgroundColor: "var(--info)", color: "#fff" }} title={JSON.stringify(lift)}>
          {busy ? "Resuming…" : "Raise by 50% and resume"}
        </button>
      )}
    </div>
  );
}

function ApprovalCard({ a }: { a: Approval }) {
  const decide = useStore((s) => s.decide);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<ApprovalDecision | null>(null);
  const go = async (d: ApprovalDecision) => {
    setBusy(d);
    try { await decide(a.id, d, note); } catch { /* shown by the store */ } finally { setBusy(null); }
  };
  const btn = "rounded px-3 py-1.5 text-sm font-medium disabled:opacity-50";
  return (
    <article aria-label={`approval ${a.id}`} className="flex flex-col gap-2 rounded-md border p-3" style={{ borderColor: "var(--line)" }}>
      <p className="text-sm"><span className="font-medium">{a.worker}</span> <span className="tone-muted">asks to run</span></p>
      <pre className="mono overflow-x-auto whitespace-pre-wrap break-all rounded p-2 text-xs" style={{ backgroundColor: "var(--bg)" }}>{a.command}</pre>
      <p className="text-xs tone-muted">Denied because: {a.reason}</p>
      <input aria-label="note for the worker" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the worker (optional)"
        className="rounded border px-2 py-1 text-sm" style={{ borderColor: "var(--line)", backgroundColor: "var(--bg)" }} />
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy !== null} onClick={() => void go("allow")} className={btn} style={{ backgroundColor: "var(--info)", color: "#fff" }}>
          {busy === "allow" ? "…" : "Allow this command"}
        </button>
        {a.program && (
          <button type="button" disabled={busy !== null} onClick={() => void go("program")} className={`${btn} border`} style={{ borderColor: "var(--info)", color: "var(--info)" }}
            title={`Adds Bash(${a.program}:*) to ${a.worker}'s grants`}>
            {busy === "program" ? "…" : `Allow all ${a.program} commands`}
          </button>
        )}
        <button type="button" disabled={busy !== null} onClick={() => void go("deny")} className={`${btn} border`} style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>
          {busy === "deny" ? "…" : "Deny"}
        </button>
      </div>
    </article>
  );
}
