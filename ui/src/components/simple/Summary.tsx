import { fuel, overallProgress, plainDuration } from "../../plain";
import { useStore } from "../../store";
import { Bar, panel, toneClass, toneVar } from "./ui";

/** "What are we building?": the request in plain words, how long it has been working, overall progress and fuel left. */
export function Summary() {
  const swarm = useStore((s) => s.swarm);
  const status = useStore((s) => s.status);
  const live = useStore((s) => s.live);
  const progress = overallProgress(status?.workers ?? []);
  const f = status?.limits && status.usage ? fuel(status.usage.tokens, status.limits.max_tokens) : null;
  return (
    <section aria-label="what are we building" className="flex flex-col gap-3 rounded-xl border p-4" style={panel}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-xs font-semibold uppercase tracking-wide tone-muted">What are we building?</h2>
          <p className="mt-1 text-lg font-semibold">{swarm ? swarm.task : "Nothing yet — start a task to see it here."}</p>
        </div>
        <span role="status" aria-label="live updates" className={`text-xs ${live ? "tone-ok" : "tone-muted"}`}>● {live ? "Live" : "Connecting…"}</span>
      </div>
      {status && (
        <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
          <div aria-label="time working">
            <p className="tone-muted">Working for</p>
            <p className="font-medium">{status.usage ? plainDuration(status.usage.wall_s) : "—"}</p>
          </div>
          <div className="flex flex-col gap-1">
            <p className="tone-muted">Overall progress <span className="font-medium" style={{ color: "var(--text)" }}>{Math.round(progress * 100)}%</span></p>
            <Bar value={progress} label="overall progress" color="var(--ok)" />
          </div>
          {f && (
            <div className="flex flex-col gap-1" title="The amount of AI work this task is allowed to use. Only you can add more.">
              <p className="tone-muted">Fuel <span className={`font-medium ${toneClass(f.tone)}`} aria-label="fuel">{f.text}</span></p>
              <Bar value={f.share} label="fuel used" color={toneVar(f.tone)} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
