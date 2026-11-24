import type { BoardEvent, EventKind, TaskStatus } from "./types";

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
    case "critique":
      return e.body.startsWith("blocker") ? "bad" : e.body.startsWith("ok") ? "ok" : "warn";
    case "nudge":
      return "warn";
    case "mention":
    case "research":
    case "decision":
      return "info";
    case "human":
      return "human";
    default:
      return "muted";
  }
}

/** 1234 -> 1.2k, 1234567 -> 1.23M (same rounding as the server's report). */
export function fmtTokens(n: number): string {
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`.replace(".0k", "k");
  return `${(n / 1_000_000).toFixed(2)}M`.replace(".00M", "M");
}

export function summarize(workers: { tokens: { total: number; cached: number }; task: { status: string } | null }[]) {
  const done = workers.filter((w) => w.task?.status === "done").length;
  const tokens = workers.reduce((a, w) => a + w.tokens.total, 0);
  const cached = workers.reduce((a, w) => a + w.tokens.cached, 0);
  return { total: workers.length, done, tokens, cached };
}

/** "3/40 wake-ups · 120k/3M tokens" style budget line; each part turns warn at 80% and bad at 100%. */
export function budgetParts(b: { limits?: { max_wakeups: number; max_tokens: number; max_workers: number }; usage?: { wakeups: number; tokens: number; workers: number } } | undefined) {
  if (!b?.limits || !b.usage) return [];
  const part = (label: string, used: number, cap: number, fmt: (n: number) => string) => {
    const r = cap > 0 ? used / cap : 0;
    return { label, text: `${fmt(used)}/${fmt(cap)}`, tone: r >= 1 ? "bad" : r >= 0.8 ? "warn" : "muted" } as const;
  };
  return [
    part("wake-ups", b.usage.wakeups, b.limits.max_wakeups, (n) => String(n)),
    part("tokens", b.usage.tokens, b.limits.max_tokens, fmtTokens),
    part("workers", b.usage.workers, b.limits.max_workers, (n) => String(n)),
  ];
}

/** "@target rest" -> target; mentions are how agents talk to each other. */
export function mentionTarget(e: Pick<BoardEvent, "kind" | "body">): string | null {
  if (e.kind !== "mention") return null;
  return /^@([A-Za-z0-9_.-]+)/.exec(e.body)?.[1] ?? null;
}

/** The agent whose words an event carries: swarmd posts a worker's turn reply on its behalf (refs: reply:<worker>). */
export function speaker(e: Pick<BoardEvent, "agent" | "refs">): string {
  const r = e.refs.find((x) => x.startsWith("reply:"));
  return r ? r.slice(6) : e.agent;
}

export function isReply(e: Pick<BoardEvent, "refs">): boolean {
  return e.refs.some((x) => x.startsWith("reply:"));
}

/** Everything one agent said, plus every mention addressed to it, in order. */
export function threadFor(events: BoardEvent[], agent: string): BoardEvent[] {
  return events.filter((e) => speaker(e) === agent || mentionTarget(e) === agent);
}

export function agentsIn(events: BoardEvent[], workers: string[]): { name: string; count: number }[] {
  const names = new Set<string>(workers);
  for (const e of events) { names.add(speaker(e)); const t = mentionTarget(e); if (t) names.add(t); }
  names.delete("swarmd");
  return [...names].sort().map((name) => ({ name, count: threadFor(events, name).length }));
}

export interface PatchLine { kind: "hunk" | "add" | "del" | "ctx" | "meta"; text: string; oldNo: number | null; newNo: number | null }

/** Turn a unified-diff body (starting at the first `@@`) into lines with old/new line numbers. */
export function parsePatch(patch: string): PatchLine[] {
  const out: PatchLine[] = [];
  let o = 0;
  let n = 0;
  const lines = patch.split("\n");
  if (lines.at(-1) === "") lines.pop();
  for (const text of lines) {
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (h) {
      o = Number(h[1]);
      n = Number(h[2]);
      out.push({ kind: "hunk", text, oldNo: null, newNo: null });
    } else if (text.startsWith("+")) out.push({ kind: "add", text, oldNo: null, newNo: n++ });
    else if (text.startsWith("-")) out.push({ kind: "del", text, oldNo: o++, newNo: null });
    else if (text.startsWith("\\")) out.push({ kind: "meta", text, oldNo: null, newNo: null }); // "\ No newline at end of file"
    else out.push({ kind: "ctx", text, oldNo: o++, newNo: n++ });
  }
  return out;
}

/** A critic location is `path:line`; critics sometimes give the path relative to a sub-folder. */
export function findingTarget(location: string): { path: string; line: number | null } {
  const m = /^(.*?):(\d+)$/.exec(location.trim());
  return m ? { path: m[1]!.replace(/^\.\//, ""), line: Number(m[2]) } : { path: location.trim().replace(/^\.\//, ""), line: null };
}

export function findingMatchesFile(location: string, filePath: string): boolean {
  const { path } = findingTarget(location);
  return path !== "" && (filePath === path || filePath.endsWith("/" + path));
}

export interface ReplayAgent {
  name: string;
  task: TaskStatus | null; // null for agents without a task (coordinator, verifiers, critics, researchers)
  last: BoardEvent;
  events: number;
  tokens: number; // input + output, from the count reported with each turn reply
  turns: number;
}
export interface ReplayState {
  at: BoardEvent | null;
  elapsedS: number;
  agents: ReplayAgent[];
  tokens: number;
  tasks: number;
  tasksDone: number;
}

const TASK_STATES: readonly string[] = ["open", "awaiting_verification", "verifying", "failed", "done", "escalated"];
const NOT_ACTORS: ReadonlySet<string> = new Set(["swarmd", "monitor", "human"]);
export const KEY_KINDS: ReadonlySet<EventKind> = new Set<EventKind>(["blocked", "done", "verify", "nudge", "decision"]);

/** Task changes an event carries. swarmd tags them (`task:<worker>:<status>`); older verify events are read from their body. */
function taskChanges(e: BoardEvent): [string, TaskStatus][] {
  const out: [string, TaskStatus][] = [];
  for (const r of e.refs) {
    const m = /^task:(.+):([a-z_]+)$/.exec(r);
    if (m && TASK_STATES.includes(m[2]!)) out.push([m[1]!, m[2] as TaskStatus]);
  }
  if (out.length === 0 && e.kind === "verify") {
    const m = /^(pass|fail) ([A-Za-z0-9_.-]+):/.exec(e.body);
    if (m) out.push([m[2]!, m[1] === "pass" ? "done" : "failed"]);
  }
  if (e.kind === "done") out.push([e.agent, "awaiting_verification"]); // a worker's `done` is provisional
  return out;
}

/**
 * The swarm as it stood right after `events[index]`, rebuilt from the board alone.
 * `taskWorkers` are the agents that own a task (so they start as `open`); the rest never show a task.
 */
export function replayState(events: BoardEvent[], index: number, taskWorkers: readonly string[] = []): ReplayState {
  const upTo = events.slice(0, Math.max(0, Math.min(index, events.length - 1)) + 1);
  const owners = new Set(taskWorkers);
  const agents = new Map<string, ReplayAgent>();
  const touch = (name: string, e: BoardEvent): ReplayAgent | null => {
    if (NOT_ACTORS.has(name)) return null;
    let a = agents.get(name);
    if (!a) {
      a = { name, task: owners.has(name) ? "open" : null, last: e, events: 0, tokens: 0, turns: 0 };
      agents.set(name, a);
    }
    return a;
  };
  let tokens = 0;
  for (const e of upTo) {
    const who = touch(speaker(e), e);
    if (who) { who.last = e; who.events += 1; }
    const target = mentionTarget(e);
    if (target) touch(target, e);
    const [tin, tout] = (e.refs.find((r) => r.startsWith("tokens:"))?.slice(7) ?? "").split(":").map((x) => Number(x) || 0); // tokens:<input>:<output>:<cached>
    const used = (tin ?? 0) + (tout ?? 0);
    const turn = Number(e.refs.find((r) => r.startsWith("turn:"))?.slice(5) ?? 0) || 0;
    tokens += used;
    if (who) { who.tokens += used; who.turns = Math.max(who.turns, turn); }
    for (const [name, status] of taskChanges(e)) {
      const a = agents.get(name) ?? (owners.has(name) ? touch(name, e) : null);
      if (a && (a.task !== null || owners.has(name) || e.kind !== "done")) a.task = status;
    }
  }
  const at = upTo.at(-1) ?? null;
  const list = [...agents.values()].sort((a, b) => Number(b.task !== null) - Number(a.task !== null) || a.name.localeCompare(b.name));
  const withTask = list.filter((a) => a.task !== null);
  return {
    at, agents: list, tokens,
    elapsedS: at && events[0] ? Math.max(0, Math.round((Date.parse(at.ts) - Date.parse(events[0].ts)) / 1000)) : 0,
    tasks: withTask.length, tasksDone: withTask.filter((a) => a.task === "done").length,
  };
}

/** Index of the nearest key moment (blocked, done, verdict, nudge, decision) before or after `index`; -1 if none. */
export function nextKeyMoment(events: BoardEvent[], index: number, dir: 1 | -1): number {
  for (let i = index + dir; i >= 0 && i < events.length; i += dir) if (KEY_KINDS.has(events[i]!.kind)) return i;
  return -1;
}

export function duration(s: number): string {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
