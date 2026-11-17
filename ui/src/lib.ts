import type { BoardEvent, EventKind } from "./types";

/** Merge incoming events into an existing list: dedupe by seq, keep ascending order. */
export function mergeEvents(existing: BoardEvent[], incoming: BoardEvent[]): BoardEvent[] {
  if (incoming.length === 0) return existing;
  const bySeq = new Map<number, BoardEvent>();
  for (const e of existing) bySeq.set(e.seq, e);
  for (const e of incoming) bySeq.set(e.seq, e);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

export function filterEvents(events: BoardEvent[], kinds: ReadonlySet<EventKind>, agent: string | null): BoardEvent[] {
  return events.filter((e) => (kinds.size === 0 || kinds.has(e.kind)) && (agent === null || e.agent === agent));
}

export function timeAgo(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

export function clock(iso: string): string {
  return iso.slice(11, 19);
}

/** Tone classes keyed by event kind; verify events are green for pass and red for fail. */
export function eventTone(e: Pick<BoardEvent, "kind" | "body">): "ok" | "bad" | "warn" | "info" | "human" | "muted" {
  switch (e.kind) {
    case "done":
      return "ok";
    case "blocked":
      return "bad";
    case "verify":
      return e.body.startsWith("pass") ? "ok" : "bad";
    case "nudge":
      return "warn";
    case "mention":
      return "info";
    case "human":
      return "human";
    default:
      return "muted";
  }
}

export function summarize(workers: { costUsd: number; task: { status: string } | null }[]) {
  const done = workers.filter((w) => w.task?.status === "done").length;
  const cost = workers.reduce((a, w) => a + w.costUsd, 0);
  return { total: workers.length, done, cost };
}

/** "3/40 wake-ups · $0.12/$10" style budget line; each part turns warn at 80% and bad at 100%. */
export function budgetParts(b: { limits?: { max_wakeups: number; max_cost_usd: number; max_workers: number }; usage?: { wakeups: number; cost_usd: number; workers: number } } | undefined) {
  if (!b?.limits || !b.usage) return [];
  const part = (label: string, used: number, cap: number, fmt: (n: number) => string) => {
    const r = cap > 0 ? used / cap : 0;
    return { label, text: `${fmt(used)}/${fmt(cap)}`, tone: r >= 1 ? "bad" : r >= 0.8 ? "warn" : "muted" } as const;
  };
  return [
    part("wake-ups", b.usage.wakeups, b.limits.max_wakeups, (n) => String(n)),
    part("spend", b.usage.cost_usd, b.limits.max_cost_usd, (n) => `$${Number.isInteger(n) ? n : n.toFixed(2)}`),
    part("workers", b.usage.workers, b.limits.max_workers, (n) => String(n)),
  ];
}
