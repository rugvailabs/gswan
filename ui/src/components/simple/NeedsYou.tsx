import { useState } from "react";
import { raiseExhausted } from "../../lib";
import { buildersOf, describeCommand, partName, roleName } from "../../plain";
import { useStore } from "../../store";
import type { Approval, ApprovalDecision, WorkerView } from "../../types";
import { inputCls, inputStyle, primaryBtn, primaryStyle, secondaryBtn, secondaryStyle } from "./ui";

/** Everything waiting for a person, in plain words, at the top of the Overview. Hidden when nothing is needed. */
export function NeedsYou() {
  const status = useStore((s) => s.status);
  const pending = status?.pendingApprovals ?? [];
  const paused = status?.pause?.paused ?? false;
  const stuck = buildersOf(status?.workers ?? []).filter((w) => w.task?.status === "escalated");
  if (!pending.length && !paused && !stuck.length) return null;
  const count = pending.length + (paused ? 1 : 0) + stuck.length;
  return (
    <section aria-label="needs your attention" className="flex flex-col gap-3 rounded-xl border-2 p-4" style={{ backgroundColor: "var(--panel)", borderColor: "var(--warn)" }}>
      <h2 className="text-base font-semibold"><span className="tone-warn">●</span> The team needs you ({count})</h2>
      {paused && <Paused />}
      {pending.map((a) => <Ask key={a.id} a={a} />)}
      {stuck.map((w) => <Stuck key={w.name} w={w} />)}
    </section>
  );
}

function Paused() {
  const status = useStore((s) => s.status);
  const setLimits = useStore((s) => s.setLimits);
  const [busy, setBusy] = useState(false);
  const lift = raiseExhausted(status ?? undefined);
  const go = async () => {
    setBusy(true);
    try { await setLimits(lift); } catch { /* shown by the store */ } finally { setBusy(false); }
  };
  return (
    <div role="alert" aria-label="work is paused" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3" style={{ borderColor: "var(--line)" }}>
      <div className="min-w-0 text-sm">
        <p className="font-medium">Work is paused: the team used up the amount of work it was allowed.</p>
        <p className="tone-muted">Nothing is lost. It continues exactly where it stopped once you allow more.</p>
      </div>
      {Object.keys(lift).length > 0 && (
        <button type="button" disabled={busy} onClick={() => void go()} className={primaryBtn} style={primaryStyle}>
          {busy ? "Continuing…" : "Give it 50% more and continue"}
        </button>
      )}
    </div>
  );
}

function Ask({ a }: { a: Approval }) {
  const decide = useStore((s) => s.decide);
  const [busy, setBusy] = useState<ApprovalDecision | null>(null);
  const d = describeCommand(a.command);
  const go = async (x: ApprovalDecision) => {
    setBusy(x);
    try { await decide(a.id, x); } catch { /* shown by the store */ } finally { setBusy(null); }
  };
  return (
    <article aria-label={`permission request from ${partName(a.worker)}`} className="flex flex-col gap-2 rounded-lg border p-3" style={{ borderColor: "var(--line)" }}>
      <p className="text-sm">
        <span className="font-medium">{roleName(a.worker)}</span> wants to run <code className="mono rounded px-1" style={{ backgroundColor: "var(--bg)" }}>{a.command.length > 100 ? `${a.command.slice(0, 99)}…` : a.command}</code>
      </p>
      <p className="text-sm">This {d.what}.{d.risk === "high" && <span className="tone-bad font-medium"> Be careful: this one can change things outside the project.</span>}</p>
      <details className="text-xs tone-muted">
        <summary className="cursor-pointer">Why was it stopped?</summary>
        <p className="mt-1">Builders can only run a short list of safe commands on their own. Reason given: {a.reason}</p>
      </details>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy !== null} onClick={() => void go("allow")} className={primaryBtn} style={primaryStyle}>{busy === "allow" ? "…" : "Approve"}</button>
        {a.program && (
          <button type="button" disabled={busy !== null} onClick={() => void go("program")} className={secondaryBtn} style={secondaryStyle}
            title={`Lets ${roleName(a.worker)} run any ${a.program} command without asking again`}>
            {busy === "program" ? "…" : `Always allow ${a.program}`}
          </button>
        )}
        <button type="button" disabled={busy !== null} onClick={() => void go("deny")} className={secondaryBtn} style={{ ...secondaryStyle, color: "var(--bad)" }}>{busy === "deny" ? "…" : "Say no"}</button>
      </div>
    </article>
  );
}

function Stuck({ w }: { w: WorkerView }) {
  const send = useStore((s) => s.sendMention);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const go = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try { await send(w.name, text.trim()); setText(""); setSent(true); } catch { /* shown by the store */ } finally { setBusy(false); }
  };
  const reasons = w.task?.reasons ?? [];
  return (
    <article aria-label={`${partName(w.name)} is stuck`} className="flex flex-col gap-2 rounded-lg border p-3" style={{ borderColor: "var(--line)" }}>
      <p className="text-sm"><span className="font-medium">{partName(w.name)}</span> is stuck and needs your direction.</p>
      {reasons.length > 0 && (
        <ul className="list-disc pl-5 text-sm tone-muted">
          {reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); void go(); }}>
        <label className="sr-only" htmlFor={`stuck-${w.name}`}>Tell {partName(w.name)} what to do</label>
        <input id={`stuck-${w.name}`} value={text} onChange={(e) => { setText(e.target.value); setSent(false); }} placeholder="Tell it what to do…" className={inputCls} style={inputStyle} />
        <button type="submit" disabled={busy || !text.trim()} className={primaryBtn} style={primaryStyle}>{busy ? "Sending…" : "Tell it what to do"}</button>
      </form>
      {sent && <p role="status" className="text-xs tone-ok">Sent. It will read your message and try again.</p>}
    </article>
  );
}
