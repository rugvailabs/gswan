/**
 * Plain-English view of a run, for people who do not know what a worktree, verifier or token is.
 * Everything here is pure (no React, no fetch) so it is unit-tested on its own.
 *
 * Board formats this reads (from swarmd/*.py):
 * - plan (coordinator): "worker <name>: <goal> | owns a, b" / "researcher <name>: <goal>"
 * - verify: "pass <w>: <reasons>" / "fail <w>: 1. r | 2. r" (refs task:<w>:done|failed)
 * - critique: "<blocker|major|nit|ok> <target>[ @ file:line]: <reason>" (target "plan" = the plan critic)
 * - nudge (monitor): "<w> <hung|looping>: <detail>..." / "<w> forgot to report; ..." / "restarting <w> (<state>: ...)"
 * - blocked: "paused: <reason>", "<w> failed verification N times; needs a human. Last reasons: ...",
 *   "verifier for <w> gave no verdict ...", "<w> <state> and the monitor ladder is exhausted ...", "worker <w> turn failed: ..."
 * - update (swarmd): "<w> replied: <text>" (refs reply:<w>, tokens:...), "verifying <w> (round n)", "critics reviewing <w> ...",
 *   "N critic blocker(s) on <w> ...", "N critic major(s) on <w> ...", "merged N into <branch>", "merge conflict in ...", ...
 * - mention: "@<target> <message>" (refs to:<name> per recipient)
 * - decision: "[topic subject] why"
 * - approval: "<id> <w> asks to run: <cmd> (denied: <reason>)" / "<id> <by> allowed this command|granted `p` (all its commands)|denied it for <w>: <cmd>"
 */
import type { BoardEvent, MonitorFinding, ResultFile, ResultFileStatus, ResultInfo, StatusResponse, TaskStatus, WorkerView } from "./types";

export type Tone = "good" | "bad" | "info" | "warn";
export interface Plain { icon: string; text: string; tone: Tone; detail?: string }

const ACRONYMS: ReadonlySet<string> = new Set(["api", "ui", "ux", "db", "sql", "css", "html", "js", "ts", "jsx", "tsx", "cli", "url", "id", "ai", "json", "http", "io", "ci", "pdf", "csv", "sdk", "jwt", "seo"]);

/** `auth-api` -> "Auth API", `login_screen` -> "Login Screen". */
export function partName(name: string): string {
  return name
    .split(/[-_.\s]+/)
    .filter(Boolean)
    .map((w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

export type RoleKind = "lead" | "you" | "system" | "builder" | "checker" | "reviewer" | "plan-reviewer" | "researcher" | "combiner";

/** Researchers have no task and own nothing; newer servers send `role` directly. */
export function isResearcher(w: Pick<WorkerView, "name" | "task" | "owns"> & { role?: string | null }): boolean {
  if (w.role) return w.role === "researcher";
  return w.task === null && w.owns.length === 0 && w.name !== "plan-critic" && w.name !== "integrator";
}

export function roleOf(name: string, researchers: ReadonlySet<string> = new Set()): RoleKind {
  if (name === "coordinator" || name === "monitor") return "lead";
  if (name === "human") return "you";
  if (name === "swarmd") return "system";
  if (name === "integrator") return "combiner";
  if (name === "plan-critic") return "plan-reviewer";
  if (name.startsWith("verify-")) return "checker";
  if (name.startsWith("critic-") || name === "critics") return "reviewer";
  if (researchers.has(name)) return "researcher";
  return "builder";
}

/** Friendly name for any agent on the board. */
export function roleName(name: string, researchers: ReadonlySet<string> = new Set()): string {
  switch (roleOf(name, researchers)) {
    case "lead": return "Team lead";
    case "you": return "You";
    case "system": return "The system";
    case "combiner": return "Combiner";
    case "plan-reviewer": return "Plan reviewer";
    case "checker": return "Checker";
    case "reviewer": return "Reviewer";
    case "researcher": return `Researcher ${partName(name)}`;
    case "builder": return `Builder ${partName(name)}`;
  }
}

/** How a part is called inside a sentence: the combined result for the integrator, otherwise its part name. */
function subject(name: string): string {
  if (name === "integrator") return "the combined project";
  if (name === "plan") return "the plan";
  return partName(name);
}

const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const clip = (s: string, n = 160) => {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t;
};

export function researchersOf(workers: readonly (Pick<WorkerView, "name" | "task" | "owns"> & { role?: string | null })[]): Set<string> {
  return new Set(workers.filter(isResearcher).map((w) => w.name));
}

function recipients(e: BoardEvent): string[] {
  const to = e.refs.filter((r) => r.startsWith("to:")).map((r) => r.slice(3));
  if (to.length) return to;
  const m = /^@([A-Za-z0-9_.-]+)/.exec(e.body);
  return m ? [m[1]!] : [];
}

function listNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

/** One plain sentence for a board event; the raw body stays available as `detail`. */
export function explainEvent(e: BoardEvent, workers: readonly (Pick<WorkerView, "name" | "task" | "owns"> & { role?: string | null })[] = []): Plain {
  const r = researchersOf(workers);
  const who = roleName(e.agent, r);
  const out = (icon: string, text: string, tone: Tone): Plain => ({ icon, text, tone, detail: e.body });
  const b = e.body.trim();
  let m: RegExpExecArray | null;
  switch (e.kind) {
    case "plan": {
      if ((m = /^worker ([A-Za-z0-9_.-]+): (.*?)(?: \| owns .*)?$/s.exec(b))) return out("◆", `The team lead planned a part, ${partName(m[1]!)}: ${clip(m[2]!)}`, "info");
      if ((m = /^researcher ([A-Za-z0-9_.-]+): (.*)$/s.exec(b))) return out("◆", `The team lead asked a researcher to look into: ${clip(m[2]!)}`, "info");
      return out("◆", `${who} shared the plan: ${clip(b)}`, "info");
    }
    case "claim":
      return out("▶", `${who} started working${b ? `: ${clip(b)}` : ""}`, "info");
    case "update":
      return explainUpdate(e, b, who, r, out);
    case "blocked": {
      if ((m = /^paused: (.*)$/s.exec(b))) return out("‖", "Work is paused because it used up its allowance. You can give it more to continue.", "warn");
      if ((m = /^([A-Za-z0-9_.-]+) failed verification (\d+) times/.exec(b))) return out("!", `${subject(m[1]!)} failed the check ${m[2]} times and needs your help`.replace(/^./, (c) => c.toUpperCase()), "bad");
      if ((m = /^verifier for ([A-Za-z0-9_.-]+) gave no verdict/.exec(b))) return out("!", `The checker could not decide about ${subject(m[1]!)}, so it needs you`, "bad");
      if ((m = /^([A-Za-z0-9_.-]+) (hung|looping|forgot)\b.*monitor ladder is exhausted/s.exec(b))) return out("!", `${cap(subject(m[1]!))} is stuck and needs you`, "bad");
      if ((m = /^worker ([A-Za-z0-9_.-]+)(?: turn failed)?: (.*)$/s.exec(b))) return out("!", `${cap(subject(m[1]!))} ran into an error: ${clip(m[2]!, 120)}`, "bad");
      if (e.agent === "swarmd" || e.agent === "monitor") return out("!", `Something went wrong: ${clip(b, 140)}`, "bad");
      return out("!", `${who} is stuck: ${clip(b)}`, "warn");
    }
    case "done":
      if (e.agent === "integrator") return out("✓", "All parts were put together. Now the whole thing gets checked.", "good");
      return out("✓", `${who} says it is finished. Next, a checker looks at it.`, "good");
    case "verify": {
      if ((m = /^pass ([A-Za-z0-9_.-]+):\s*(.*)$/s.exec(b))) return out("✓", `The checker approved ${subject(m[1]!)}`, "good");
      if ((m = /^fail ([A-Za-z0-9_.-]+):\s*(.*)$/s.exec(b))) {
        const first = m[2]!.split(" | ")[0]!.replace(/^\d+\.\s*/, "");
        return out("✕", `The checker found problems in ${subject(m[1]!)} and sent it back: ${clip(first, 140)}`, "bad");
      }
      return out("✓", `${who} checked the work: ${clip(b)}`, "info");
    }
    case "nudge": {
      if ((m = /^restarting ([A-Za-z0-9_.-]+)/.exec(b))) return out("↻", `${cap(subject(m[1]!))} was stuck, so the team lead restarted it`, "warn");
      if ((m = /^([A-Za-z0-9_.-]+) forgot to report/.exec(b))) return out("↻", `${cap(subject(m[1]!))} stopped without saying whether it was done, so the team lead reminded it`, "warn");
      if ((m = /^([A-Za-z0-9_.-]+) looping\b/.exec(b))) return out("↻", `${cap(subject(m[1]!))} kept repeating itself, so the team lead gave it a nudge`, "warn");
      if ((m = /^([A-Za-z0-9_.-]+) (hung|\w+)\b/.exec(b))) return out("↻", `${cap(subject(m[1]!))} went quiet, so the team lead gave it a nudge`, "warn");
      return out("↻", `The team lead gave a nudge: ${clip(b)}`, "warn");
    }
    case "mention": {
      const to = recipients(e);
      const names = to.includes("all") ? "everyone" : listNames(to.map((t) => roleName(t, r)));
      const text = clip(b.replace(/^@[A-Za-z0-9_.-]+\s*/, ""), 140);
      if (e.agent === "human") return out("→", `You asked ${names || "the team"}: ${text}`, "info");
      return out("→", `${who} wrote to ${names || "the team"}: ${text}`, "info");
    }
    case "human":
      if (/^Ran the project: /.test(b)) return out("▶", "You started the project on your computer", "info");
      return out("✎", `You left a note for the team: ${clip(b)}`, "info");
    case "research":
      return out("⌕", `${who} found: ${clip(b)}`, "info");
    case "critique": {
      if ((m = /^(blocker|major|nit|ok) ([A-Za-z0-9_.-]+)(?: @ [^:]+(?::\d+)?)?:\s*(.*)$/s.exec(b))) {
        const [, sev, target, reason] = m;
        const what = subject(target!);
        const by = e.agent === "plan-critic" ? "The plan reviewer" : "A reviewer";
        if (sev === "ok") return out("◎", `${by} looked at ${what} and found nothing serious`, "good");
        if (sev === "blocker") return out("◎", `${by} found a serious problem in ${what}: ${clip(reason!, 140)}`, "bad");
        if (sev === "major") return out("◎", `${by} found something to fix in ${what}: ${clip(reason!, 140)}`, "warn");
        return out("◎", `${by} made a small suggestion for ${what}: ${clip(reason!, 140)}`, "info");
      }
      return out("◎", `${who} reviewed the work: ${clip(b)}`, "info");
    }
    case "decision": {
      m = /^\[[a-z]+(?: [A-Za-z0-9_.-]+)?\]\s*(.*)$/s.exec(b);
      return out("◇", `${who} made a decision: ${clip(m ? m[1]! : b)}`, "info");
    }
    case "approval": {
      if ((m = /^\S+ ([A-Za-z0-9_.-]+) asks to run: (.*?)(?: \(denied: .*\))?$/s.exec(b))) {
        return out("?", `${roleName(m[1]!, r)} ${humanizeCommand(m[2]!)}. It needs your OK.`, "warn");
      }
      if ((m = /^\S+ ([A-Za-z0-9_.-]+) (allowed this command|granted `([^`]+)` \(all its commands\)|denied it) for ([A-Za-z0-9_.-]+): (.*?)(?: — .*)?$/s.exec(b))) {
        const [, by, what, prog, worker, cmd] = m;
        const actor = by === "human" ? "You" : roleName(by!, r);
        const target = roleName(worker!, r);
        if (what!.startsWith("allowed")) return out("✓", `${actor} allowed ${target} to run \`${clip(cmd!, 80)}\``, "good");
        if (what!.startsWith("granted")) return out("✓", `${actor} let ${target} use \`${prog}\` from now on`, "good");
        return out("✕", `${actor} said no to ${target} running \`${clip(cmd!, 80)}\``, "bad");
      }
      return out("?", `Permission update: ${clip(b)}`, "info");
    }
  }
  return out("•", clip(b), "info");
}

function explainUpdate(e: BoardEvent, b: string, who: string, r: ReadonlySet<string>, out: (icon: string, text: string, tone: Tone) => Plain): Plain {
  let m: RegExpExecArray | null;
  const reply = e.refs.find((x) => x.startsWith("reply:"))?.slice(6);
  if (reply) {
    const text = b.startsWith(`${reply} replied: `) ? b.slice(reply.length + 10) : b;
    return out("“", `${roleName(reply, r)} said: ${clip(text)}`, "info");
  }
  if ((m = /^verifying ([A-Za-z0-9_.-]+) \(round (\d+)\)/.exec(b))) return out("…", `A checker is now testing ${subject(m[1]!)}${Number(m[2]) > 1 ? ` (try ${m[2]})` : ""}`, "info");
  if ((m = /^verifier for ([A-Za-z0-9_.-]+) runs on/.exec(b))) return out("…", `A checker was picked for ${subject(m[1]!)}`, "info");
  if ((m = /^critics reviewing ([A-Za-z0-9_.-]+)/.exec(b))) return out("◎", `Reviewers are reading the work on ${subject(m[1]!)}`, "info");
  if ((m = /^critics skipped for ([A-Za-z0-9_.-]+)/.exec(b))) return out("…", `${cap(subject(m[1]!))} was a small change, so it goes straight to the checker`, "info");
  if ((m = /^(\d+) critic blocker\(s\) on ([A-Za-z0-9_.-]+)/.exec(b))) return out("✕", `Reviewers found ${m[1]} serious problem${m[1] === "1" ? "" : "s"} in ${subject(m[2]!)} and sent it back`, "bad");
  if ((m = /^(\d+) critic major\(s\) on ([A-Za-z0-9_.-]+)/.exec(b))) return out("↩", `Reviewers asked ${subject(m[2]!)} to fix or explain ${m[1]} thing${m[1] === "1" ? "" : "s"}`, "warn");
  if ((m = /^merged (\d+) into /.exec(b))) return out("⧉", `Put ${m[1]} part${m[1] === "1" ? "" : "s"} together into one copy`, "good");
  if (/^merge conflict in /.test(b)) return out("⧉", "Two parts changed the same files. The combiner is sorting it out.", "warn");
  if ((m = /^([A-Za-z0-9_.-]+) paused before verification/.exec(b))) return out("‖", `${cap(subject(m[1]!))} is waiting until you allow more work`, "warn");
  if (/^budget allows work again/.test(b)) return out("▶", "Work picked up again where it stopped", "good");
  if ((m = /^worker ([A-Za-z0-9_.-]+) turn was terminated/.exec(b))) return out("↻", `${cap(subject(m[1]!))} was stopped and will start again`, "warn");
  if ((m = /^([A-Za-z0-9_.-]+)'s turn was cut off because the computer slept/.exec(b))) return out("☾", `The computer went to sleep; ${subject(m[1]!)} picks up where it left off`, "info");
  if (/^machine slept for /.test(b)) return out("☾", "The computer was asleep for a while. That time does not count.", "info");
  if ((m = /^worker ([A-Za-z0-9_.-]+) was denied: /.exec(b))) return out("✋", `${cap(subject(m[1]!))} tried something it is not allowed to do on its own`, "warn");
  if ((m = /^([A-Za-z0-9_.-]+) quiet for \d+s but judged still progressing/.exec(b))) return out("…", `${cap(subject(m[1]!))} is quiet but still making progress`, "info");
  return out("•", `${who}: ${clip(b)}`, "info");
}

// ---- journey -------------------------------------------------------------------------------------------------

export type StageKey = "understand" | "plan" | "build" | "check" | "fix" | "combine" | "ready";
export type StageState = "done" | "active" | "upcoming" | "skipped";
export interface Stage { key: StageKey; title: string; blurb: string; state: StageState }

export const STAGES: readonly { key: StageKey; title: string; blurb: string }[] = [
  { key: "understand", title: "Understand", blurb: "The team lead reads your request and, if needed, researches what it takes." },
  { key: "plan", title: "Plan", blurb: "The work is split into parts, and each part gets its own builder." },
  { key: "build", title: "Build", blurb: "Builders write the code for their part, each in a separate draft copy." },
  { key: "check", title: "Check", blurb: "A checker tests every finished part before it counts as done." },
  { key: "fix", title: "Fix", blurb: "Parts that did not pass go back to their builder with a list of problems." },
  { key: "combine", title: "Combine", blurb: "The checked parts are put together into one copy and tested as a whole." },
  { key: "ready", title: "Ready", blurb: "The result is finished and checked. You can open, run or download it." },
];

type JourneyWorker = Pick<WorkerView, "name" | "task" | "owns" | "busy" | "status" | "turns"> & { role?: string | null };

/** Builders: the workers that own a part (not checkers, reviewers, researchers or the combiner). */
export function buildersOf<W extends Pick<WorkerView, "name" | "task" | "owns"> & { role?: string | null }>(workers: readonly W[]): W[] {
  return workers.filter((w) => roleOf(w.name, researchersOf(workers)) === "builder");
}

function everFailed(events: readonly BoardEvent[], workers: readonly JourneyWorker[]): boolean {
  if (workers.some((w) => w.task && (w.task.failedRounds > 0 || w.task.status === "failed"))) return true;
  return events.some((e) =>
    (e.kind === "verify" && e.body.startsWith("fail ")) || e.refs.some((r) => /^task:.+:failed$/.test(r)));
}

/** The seven stages of a run, with which ones are done, happening now, still ahead, or skipped (nothing to fix). */
export function journey(status: Pick<StatusResponse, "workers" | "allTasksSettled"> | null, events: readonly BoardEvent[], result: Pick<ResultInfo, "stage" | "verified"> | null): Stage[] {
  if (!status) return STAGES.map((s) => ({ ...s, state: "upcoming" }));
  const workers = status.workers as readonly JourneyWorker[];
  const builders = buildersOf(workers);
  const tasks = builders.map((w) => w.task?.status).filter((x): x is TaskStatus => !!x);
  const integrator = workers.find((w) => w.name === "integrator");
  const planCritic = workers.find((w) => w.name === "plan-critic");
  const integrated = !!integrator || result?.stage === "integration" || result?.stage === "applied";
  const combinedVerified = (result?.stage === "integration" && result.verified === true) || integrator?.task?.status === "done";
  const ready = result?.stage === "applied" || combinedVerified
    || (builders.length > 0 && tasks.length === builders.length && tasks.every((t) => t === "done") && status.allTasksSettled && !(integrator && integrator.task?.status !== "done"));

  const active: Record<StageKey, boolean> = {
    understand: builders.length === 0,
    plan: builders.length > 0 && (builders.every((w) => w.turns === 0 && !w.busy && w.status !== "running") || !!planCritic?.busy),
    build: builders.some((w) => w.task?.status === "open" && (w.busy || w.status === "running" || w.turns > 0)),
    check: builders.some((w) => w.task?.status === "awaiting_verification" || w.task?.status === "verifying")
      || workers.some((w) => (w.name.startsWith("verify-") || w.name.startsWith("critic-")) && w.busy),
    fix: builders.some((w) => w.task?.status === "failed" || w.task?.status === "escalated"),
    combine: integrated && !combinedVerified && result?.stage !== "applied",
    ready,
  };
  if (ready) for (const k of Object.keys(active) as StageKey[]) active[k] = k === "ready";
  if (!Object.values(active).some(Boolean)) active.build = true; // e.g. every part reported done but the run is not settled yet
  const reached = Math.max(...STAGES.map((s, i) => (active[s.key] ? i : -1)));
  const failed = everFailed(events, workers);
  return STAGES.map((s, i) => {
    let state: StageState;
    if (s.key === "ready" && ready) state = "done";
    else if (active[s.key]) state = "active";
    else if (i < reached) state = (s.key === "fix" && !failed) || (s.key === "combine" && !integrated) ? "skipped" : "done";
    else state = "upcoming";
    return { ...s, state };
  });
}

/** Which stage of the journey an event belongs to, so clicking a stage can show what happened in it. */
export function stageOf(e: BoardEvent): StageKey {
  const b = e.body;
  if (e.agent === "integrator" || /^merged \d+ into |^merge conflict in /.test(b) || /^(pass|fail) integrator:/.test(b)) return "combine";
  switch (e.kind) {
    case "research": return "understand";
    case "plan": return /^researcher /.test(b) ? "understand" : "plan";
    case "decision": return "plan";
    case "critique": return e.agent === "plan-critic" || /^(?:blocker|major|nit|ok) plan[ :]/.test(b) ? "plan" : "check";
    case "done": return "check";
    case "verify": return b.startsWith("fail ") ? "fix" : "check";
    case "blocked": return /failed verification|gave no verdict|ladder is exhausted/.test(b) ? "fix" : "build";
    case "update":
      if (/^\d+ critic (blocker|major)\(s\)/.test(b)) return "fix";
      if (/^verifying |^verifier for |^critics (reviewing|skipped)/.test(b)) return "check";
      return "build";
    default: return "build";
  }
}

// ---- one part (builder) ----------------------------------------------------------------------------------------

export interface WorkerStory { text: string; tone: Tone; progress: number; needsYou: boolean }

/** A builder's plain status line and a 0..1 progress value for a small bar. */
export function workerStory(w: Pick<WorkerView, "name" | "task" | "busy" | "status" | "turns" | "owns" | "lastError"> & { role?: string | null }, finding?: Pick<MonitorFinding, "state"> | null): WorkerStory {
  const t = w.task;
  if (!t) {
    if (w.busy || w.status === "running") return { text: "Looking things up", tone: "info", progress: 0.5, needsYou: false };
    return w.turns > 0 ? { text: "Finished looking things up", tone: "good", progress: 1, needsYou: false } : { text: "Waiting for its turn", tone: "info", progress: 0, needsYou: false };
  }
  const n = Math.max(1, t.reasons.length);
  switch (t.status) {
    case "done": return { text: "Finished and checked", tone: "good", progress: 1, needsYou: false };
    case "escalated": return { text: "Stuck — needs you", tone: "bad", progress: 0.6, needsYou: true };
    case "verifying": return { text: "Being checked", tone: "info", progress: 0.85, needsYou: false };
    case "awaiting_verification": return { text: "Waiting for the checker", tone: "info", progress: 0.75, needsYou: false };
    case "failed": return { text: `Sent back to fix ${n} problem${n === 1 ? "" : "s"}`, tone: "warn", progress: 0.55, needsYou: false };
    case "open": {
      if (finding && (finding.state === "hung" || finding.state === "looping")) return { text: "Went quiet — the team lead is nudging it", tone: "warn", progress: 0.4, needsYou: false };
      if (w.status === "error") return { text: "Ran into a problem", tone: "bad", progress: 0.3, needsYou: false };
      if (w.busy || w.status === "running") return { text: "Writing code", tone: "info", progress: Math.min(0.6, 0.2 + 0.1 * w.turns), needsYou: false };
      if (w.turns === 0) return { text: "Waiting for its turn", tone: "info", progress: 0.05, needsYou: false };
      return { text: "Between steps", tone: "info", progress: Math.min(0.6, 0.2 + 0.1 * w.turns), needsYou: false };
    }
  }
}

/** Goals from the plan events (`worker <name>: <goal> | owns ...`), for servers that do not send `goal`. */
export function goalsFromEvents(events: readonly BoardEvent[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const e of events) {
    if (e.kind !== "plan") continue;
    const m = /^(?:worker|researcher) ([A-Za-z0-9_.-]+): (.*?)(?: \| owns .*)?$/s.exec(e.body.trim());
    if (m) out.set(m[1]!, m[2]!.trim());
  }
  return out;
}

/** Is this event about the given part (said by it, sent to it, about its check or its task)? */
export function aboutPart(e: BoardEvent, name: string): boolean {
  if (e.agent === name || e.agent === `verify-${name}` || e.agent.startsWith(`critic-${name}-`)) return true;
  if (e.refs.some((r) => r === `reply:${name}` || r === `to:${name}` || r === `worker:${name}` || r.startsWith(`task:${name}:`))) return true;
  const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const end = "(?![A-Za-z0-9_.-])";
  if (e.kind === "mention") return new RegExp(`^@${esc}${end}`).test(e.body);
  if (e.kind === "plan") return new RegExp(`^(?:worker|researcher) ${esc}:`).test(e.body);
  if (e.kind === "approval") return new RegExp(`^\\S+ ${esc} asks to run:|for ${esc}:`).test(e.body);
  return new RegExp(`^(?:(?:pass|fail|blocker|major|nit|ok|worker|verifying|restarting|critics reviewing|critics skipped for|\\d+ critic \\w+\\(s\\) on|verifier for) )?${esc}${end}`).test(e.body);
}

/** "src/api/" -> folder "src/api", "README.md" -> file. */
export function ownedChip(path: string): { label: string; folder: boolean } {
  const p = path.trim();
  const folder = p.endsWith("/") || p.endsWith("/**") || p.endsWith("/*") || !/\.[A-Za-z0-9]+$/.test(p.split("/").at(-1) ?? "");
  return { label: p.replace(/\/\*\*?$/, "").replace(/\/$/, "") || p, folder };
}

// ---- commands people are asked to approve --------------------------------------------------------------------

export type Risk = "low" | "medium" | "high";

function words(cmd: string): string[] {
  let s = cmd.trim();
  s = s.replace(/^(cd\s+\S+\s*(&&|;)\s*)+/, "");
  const parts = s.split(/\s+/).filter(Boolean);
  while (parts.length && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(parts[0]!) || parts[0] === "env")) parts.shift();
  return parts;
}

/** What a command does, in plain words, and how careful to be with it. */
export function describeCommand(cmd: string): { what: string; risk: Risk } {
  const w = words(cmd);
  let prog = (w[0] ?? "").split("/").at(-1) ?? "";
  let args = w.slice(1);
  if (prog === "sudo") return { what: "runs a command with full control over your computer — be careful", risk: "high" };
  if (prog === "npx" || prog === "pnpx" || prog === "bunx") return { what: `downloads and runs a JavaScript tool${args[0] ? ` (${args[0]})` : ""}`, risk: "medium" };
  if ((prog === "python" || prog === "python3") && args[0] === "-m" && args[1]) { prog = args[1]; args = args.slice(2); }
  if (prog === "uv" && args[0] === "run" && args[1]) { prog = args[1]; args = args.slice(2); }
  const sub = args.find((a) => !a.startsWith("-")) ?? "";
  switch (prog) {
    case "npm": case "pnpm": case "yarn": case "bun": {
      if (["install", "i", "ci", "add"].includes(sub) || (prog === "yarn" && args.length === 0)) return { what: "downloads the libraries the project needs", risk: "medium" };
      if (sub === "test" || (sub === "run" && /test/.test(args[1] ?? ""))) return { what: "runs the project's tests", risk: "low" };
      if (["start", "dev"].includes(sub) || (sub === "run" && ["dev", "start", "serve", "preview"].includes(args[1] ?? ""))) return { what: "starts the app", risk: "low" };
      if (sub === "build" || (sub === "run" && args[1] === "build")) return { what: "builds the app so it is ready to use", risk: "low" };
      if (sub === "run" && args[1]) return { what: `runs the project's "${args[1]}" step`, risk: "low" };
      return { what: "uses the JavaScript package tool", risk: "medium" };
    }
    case "pip": case "pip3":
      return sub === "install" ? { what: "downloads the Python libraries the project needs", risk: "medium" } : { what: "uses the Python package tool", risk: "medium" };
    case "uv": case "poetry":
      if (["sync", "add", "install", "pip"].includes(sub)) return { what: "downloads the Python libraries the project needs", risk: "medium" };
      return { what: "uses the Python project tool", risk: "medium" };
    case "pytest": case "vitest": case "jest": case "unittest":
      return { what: "runs the project's tests", risk: "low" };
    case "python": case "python3": case "node": case "deno": case "ruby": {
      const lang = prog.startsWith("python") ? "Python" : prog === "ruby" ? "Ruby" : "JavaScript";
      if (args[0] === "-c" || args[0] === "-e") return { what: `runs a small piece of ${lang} code`, risk: "medium" };
      return { what: sub ? `runs the ${lang} file ${sub}` : `starts ${lang}`, risk: "medium" };
    }
    case "git": {
      if (["status", "diff", "log", "show", "blame", "branch"].includes(sub)) return { what: "looks at the history of changes", risk: "low" };
      if (["add", "commit"].includes(sub)) return { what: "saves a snapshot of its work", risk: "low" };
      if (sub === "push") return { what: "sends code somewhere else, like GitHub", risk: "high" };
      if (["pull", "fetch", "clone"].includes(sub)) return { what: "downloads code from somewhere else", risk: "medium" };
      if (["checkout", "switch", "reset", "restore", "revert", "stash", "clean", "rebase"].includes(sub)) return { what: "switches or undoes versions of files", risk: "medium" };
      if (sub === "merge") return { what: "combines two versions of the code", risk: "medium" };
      return { what: "uses Git, the tool that tracks changes", risk: "medium" };
    }
    case "curl": case "wget": {
      const url = args.find((a) => /^https?:\/\//.test(a));
      let host = "";
      try { host = url ? new URL(url).host : ""; } catch { host = ""; }
      return { what: `downloads something from the internet${host ? ` (${host})` : ""}`, risk: "medium" };
    }
    case "rm": case "rmdir": {
      const force = args.some((a) => /^-[a-zA-Z]*[rf]/.test(a));
      const target = args.filter((a) => !a.startsWith("-")).join(" ");
      return { what: `deletes ${force ? "files or folders for good" : "files"}${target ? ` (${target})` : ""}`, risk: force ? "high" : "medium" };
    }
    case "mv": return { what: "moves or renames files", risk: "medium" };
    case "cp": return { what: "copies files", risk: "low" };
    case "mkdir": return { what: "creates a folder", risk: "low" };
    case "touch": return { what: "creates an empty file", risk: "low" };
    case "chmod": case "chown": return { what: "changes who may use or run files", risk: "medium" };
    case "ls": case "cat": case "head": case "tail": case "grep": case "rg": case "find": case "wc": case "tree":
      return { what: "looks at files", risk: "low" };
    case "docker": case "podman": return { what: "starts or manages containers (small isolated computers)", risk: "medium" };
    case "make": return { what: "runs the project's build steps", risk: "medium" };
    case "cargo": return { what: sub === "test" ? "runs the project's tests" : "uses the Rust build tool", risk: sub === "test" ? "low" : "medium" };
    case "go": return { what: sub === "test" ? "runs the project's tests" : "uses the Go build tool", risk: sub === "test" ? "low" : "medium" };
    case "brew": case "apt": case "apt-get": return { what: "installs software on your computer", risk: "high" };
    case "kill": case "pkill": case "killall": return { what: "stops running programs", risk: "medium" };
    case "ssh": case "scp": return { what: "connects to another computer", risk: "high" };
    case "tsc": return { what: "checks the code for type mistakes", risk: "low" };
    case "eslint": case "ruff": case "prettier": case "black": return { what: "checks or tidies the code style", risk: "low" };
  }
  return { what: prog ? `runs the program "${prog}"` : "runs a command", risk: "medium" };
}

/** "wants to run `npm install` — downloads the libraries the project needs". */
export function humanizeCommand(cmd: string): string {
  const shown = cmd.replace(/\s+/g, " ").trim();
  const short = shown.length > 80 ? `${shown.slice(0, 79)}…` : shown;
  return `wants to run \`${short}\` — ${describeCommand(cmd).what}`;
}

// ---- the result --------------------------------------------------------------------------------------------------

export function resultHeadline(r: Pick<ResultInfo, "stage" | "verified"> | null): { text: string; tone: Tone } {
  if (!r || r.stage === "none") return { text: "Nothing built yet", tone: "info" };
  if (r.stage === "workers") return { text: "Each part is in its own draft copy", tone: "info" };
  if (r.stage === "applied") return { text: "Already added to your project", tone: "good" };
  if (r.verified === true) return { text: "All parts combined into one copy — checked ✓", tone: "good" };
  if (r.verified === false) return { text: "All parts combined into one copy — the check found problems", tone: "bad" };
  return { text: "All parts combined into one copy — being checked", tone: "info" };
}

export interface TreeNode { name: string; path: string; folder: boolean; status: ResultFileStatus | null; changed: number; children: TreeNode[] }

/** Folder tree from flat file paths. Folders first, then files, each alphabetical; `changed` counts non-unchanged files below. */
export function buildTree(files: readonly ResultFile[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", folder: true, status: null, changed: 0, children: [] };
  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    let node = root;
    parts.forEach((p, i) => {
      const path = parts.slice(0, i + 1).join("/");
      const leaf = i === parts.length - 1;
      let next = node.children.find((c) => c.name === p && c.folder === !leaf);
      if (!next) {
        next = { name: p, path, folder: !leaf, status: leaf ? f.status : null, changed: 0, children: [] };
        node.children.push(next);
      }
      if (f.status !== "unchanged") node.changed += 1;
      node = next;
    });
  }
  const sort = (n: TreeNode) => {
    n.children.sort((a, b) => Number(b.folder) - Number(a.folder) || a.name.localeCompare(b.name));
    n.children.forEach(sort);
  };
  sort(root);
  return root.children;
}

/** "12 minutes", "1 hour 5 minutes", "40 seconds". */
export function plainDuration(s: number): string {
  const sec = Math.max(0, Math.round(s));
  if (sec < 60) return `${sec} second${sec === 1 ? "" : "s"}`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"}`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return `${h} hour${h === 1 ? "" : "s"}${rm ? ` ${rm} minute${rm === 1 ? "" : "s"}` : ""}`;
}

/** The budget as fuel: share used and a plain sentence. */
export function fuel(used: number, max: number): { share: number; text: string; tone: Tone } {
  const share = max > 0 ? Math.min(1, used / max) : 0;
  const pct = Math.round(share * 100);
  if (share >= 1) return { share, text: "Out of fuel — work pauses until you add more", tone: "bad" };
  if (share >= 0.8) return { share, text: `Running low: ${pct}% of the fuel is used`, tone: "warn" };
  return { share, text: `${pct}% of the fuel is used`, tone: "good" };
}

/** Overall progress: the average of every builder's progress bar. */
export function overallProgress(workers: readonly Parameters<typeof workerStory>[0][]): number {
  const b = buildersOf(workers.map((w) => ({ ...w, owns: w.owns })));
  if (!b.length) return 0;
  return b.reduce((a, w) => a + workerStory(w).progress, 0) / b.length;
}

export const ASK_STATUS = "In one or two plain sentences, what are you doing right now and what is left?";
