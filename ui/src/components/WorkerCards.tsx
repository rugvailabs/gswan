import { timeAgo } from "../lib";
import { useStore } from "../store";
import type { MonitorFinding, TaskStatus, WorkerView } from "../types";

const TASK_TONE: Record<TaskStatus, string> = {
  open: "tone-muted",
  awaiting_verification: "tone-info",
  verifying: "tone-info",
  failed: "tone-bad",
  done: "tone-ok",
  escalated: "tone-bad",
};
const STATE_TONE: Record<MonitorFinding["state"], string> = {
  healthy: "tone-muted", waiting: "tone-info", blocked: "tone-warn", hung: "tone-bad", looping: "tone-bad", forgot: "tone-warn",
};

function Card({ w, finding }: { w: WorkerView; finding?: MonitorFinding }) {
  const setTarget = useStore((s) => s.setMentionTarget);
  const setAgent = useStore((s) => s.setAgent);
  const agent = useStore((s) => s.agent);
  const openThread = useStore((s) => s.openThread);
  const task = w.task;
  return (
    <article aria-label={`worker ${w.name}`} className="flex flex-col gap-2 rounded-lg border p-3" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="mono truncate font-semibold">{w.name}</h2>
        <span className={w.status === "running" ? "tone-ok" : w.status === "error" ? "tone-bad" : "tone-muted"}>
          {w.status === "running" ? "● running" : w.status === "error" ? "● error" : "○ idle"}
        </span>
      </div>
      {task && (
        <p className="text-sm">
          task <span className={`font-medium ${TASK_TONE[task.status]}`}>{task.status.replace("_", " ")}</span>
          {task.failedRounds > 0 && <span className="tone-bad"> · {task.failedRounds} failed round{task.failedRounds > 1 ? "s" : ""}</span>}
        </p>
      )}
      {finding && finding.state !== "healthy" && (
        <p className={`text-sm ${STATE_TONE[finding.state]}`} title={finding.detail}>monitor: {finding.state} — {finding.detail}</p>
      )}
      {w.status === "running" && <p className="mono text-xs tone-muted">{w.activityNote ?? "starting"} · {timeAgo(w.lastActivity)}</p>}
      {w.recentTools && w.recentTools.length > 0 && (
        <ul aria-label={`${w.name} recent tools`} className="mono space-y-0.5 text-xs tone-muted">
          {w.recentTools.slice(-4).map((t, i) => <li key={`${i}-${t}`} className="truncate" title={t}>{t}</li>)}
        </ul>
      )}
      {w.lastError && <p className="text-xs tone-bad">{w.lastError}</p>}
      <dl className="grid grid-cols-2 gap-x-3 text-xs tone-muted">
        <dt>owns</dt><dd className="mono truncate" title={w.owns.join(", ")}>{w.owns.join(", ")}</dd>
        <dt>model</dt><dd>{w.model}</dd>
        <dt>turns · cost</dt><dd className="mono">{w.turns} · ${w.costUsd.toFixed(3)}</dd>
        {w.queue > 0 && (<><dt>queued</dt><dd>{w.queue}</dd></>)}
        {w.branch && (<><dt>branch</dt><dd className="mono truncate" title={w.branch}>{w.branch}</dd></>)}
      </dl>
      <div className="mt-1 flex gap-2">
        <button className="rounded border px-2 py-1 text-xs" style={{ borderColor: "var(--line)" }} onClick={() => setTarget(w.name)}>@ mention</button>
        <button className="rounded border px-2 py-1 text-xs" style={{ borderColor: "var(--line)" }} aria-pressed={agent === w.name} onClick={() => setAgent(agent === w.name ? null : w.name)}>
          {agent === w.name ? "show all" : "filter board"}
        </button>
        <button className="rounded border px-2 py-1 text-xs" style={{ borderColor: "var(--line)" }} onClick={() => openThread(w.name)}>conversation</button>
      </div>
    </article>
  );
}

export function WorkerCards() {
  const status = useStore((s) => s.status);
  if (!status) return null;
  if (status.workers.length === 0) return <p className="text-sm tone-muted">No workers yet. The coordinator defines them with define_worker.</p>;
  const byName = new Map(status.monitor.map((f) => [f.worker, f]));
  return (
    <section aria-label="workers" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {status.workers.map((w) => <Card key={w.name} w={w} finding={byName.get(w.name)} />)}
    </section>
  );
}
