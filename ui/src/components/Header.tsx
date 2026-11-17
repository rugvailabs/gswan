import { budgetParts, summarize } from "../lib";
import { useStore } from "../store";

export function Header() {
  const swarm = useStore((s) => s.swarm);
  const status = useStore((s) => s.status);
  const live = useStore((s) => s.live);
  const sum = summarize(status?.workers ?? []);
  const budget = budgetParts(status ?? undefined);
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
      <div className="min-w-0">
        <h1 className="text-lg font-semibold">Swarm</h1>
        <p className="truncate text-sm tone-muted" title={swarm?.task}>{swarm ? swarm.task : "No active swarm"}</p>
      </div>
      <div className="flex items-center gap-4 text-sm">
        {status && (
          <>
            <span aria-label="tasks done">{sum.done}/{sum.total} tasks done</span>
            <span aria-label="total cost" className="mono">${sum.cost.toFixed(3)}</span>
            {budget.length > 0 && (
              <span aria-label="budget" className="mono text-xs tone-muted">
                {budget.map((p) => (
                  <span key={p.label} className={`ml-2 tone-${p.tone}`} title={`${p.label}: ${p.text}`}>{p.label} {p.text}</span>
                ))}
              </span>
            )}
            <span className={status.allTasksSettled ? "tone-ok" : "tone-warn"}>{status.allTasksSettled ? "settled" : "in progress"}</span>
          </>
        )}
        <span role="status" aria-label="connection" className={live ? "tone-ok" : "tone-bad"}>
          ● {live ? "live" : "reconnecting"}
        </span>
      </div>
    </header>
  );
}
