import { describe, expect, it } from "vitest";
import { budgetParts, eventTone, filterEvents, findingMatchesFile, findingTarget, mergeEvents, nextKeyMoment, parsePatch, replayState, summarize, timeAgo, duration } from "./lib";
import type { BoardEvent, EventKind } from "./types";

const ev = (seq: number, kind: EventKind = "update", agent = "api", body = "x"): BoardEvent =>
  ({ seq, id: `evt_${seq}`, swarmId: "swm_1", ts: "2026-11-17T12:00:00.000000Z", agent, kind, body, taskId: null, refs: [] });

describe("mergeEvents", () => {
  it("dedupes by seq and keeps ascending order", () => {
    const out = mergeEvents([ev(1), ev(3)], [ev(2), ev(3, "done")]);
    expect(out.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(out[2]?.kind).toBe("done"); // later copy wins
  });
  it("returns the same array when nothing is incoming", () => {
    const a = [ev(1)];
    expect(mergeEvents(a, [])).toBe(a);
  });
});

describe("filterEvents", () => {
  const all = [ev(1, "claim", "api"), ev(2, "done", "ui"), ev(3, "done", "api")];
  it("empty kind set means all", () => expect(filterEvents(all, new Set(), null)).toHaveLength(3));
  it("filters by kind and agent together", () => {
    expect(filterEvents(all, new Set<EventKind>(["done"]), "api").map((e) => e.seq)).toEqual([3]);
  });
});

describe("eventTone", () => {
  it("colours verify by verdict", () => {
    expect(eventTone({ kind: "verify", body: "pass api: ok" })).toBe("ok");
    expect(eventTone({ kind: "verify", body: "fail api: nope" })).toBe("bad");
  });
  it("maps kinds", () => {
    expect(eventTone({ kind: "blocked", body: "" })).toBe("bad");
    expect(eventTone({ kind: "nudge", body: "" })).toBe("warn");
    expect(eventTone({ kind: "update", body: "" })).toBe("muted");
  });
});

describe("timeAgo", () => {
  const now = Date.parse("2026-11-17T12:10:00Z");
  it("formats", () => {
    expect(timeAgo(null, now)).toBe("never");
    expect(timeAgo("2026-11-17T12:09:58Z", now)).toBe("just now");
    expect(timeAgo("2026-11-17T12:09:30Z", now)).toBe("30s ago");
    expect(timeAgo("2026-11-17T12:05:00Z", now)).toBe("5m ago");
    expect(timeAgo("2026-11-17T09:10:00Z", now)).toBe("3h ago");
  });
});

describe("summarize", () => {
  it("counts done tasks and sums cost", () => {
    expect(summarize([{ costUsd: 0.1, task: { status: "done" } }, { costUsd: 0.25, task: { status: "open" } }, { costUsd: 0, task: null }]))
      .toEqual({ total: 3, done: 1, cost: 0.35 });
  });
});

describe("budgetParts", () => {
  const limits = { max_wakeups: 40, max_cost_usd: 10, max_workers: 6 };
  it("is empty without data", () => expect(budgetParts(undefined)).toEqual([]));
  it("formats and flags thresholds", () => {
    const parts = budgetParts({ limits, usage: { wakeups: 32, cost_usd: 12.5, workers: 2 } });
    expect(parts.map((p) => p.text)).toEqual(["32/40", "$12.50/$10", "2/6"]);
    expect(parts.map((p) => p.tone)).toEqual(["warn", "bad", "muted"]);
  });
});

describe("parsePatch", () => {
  it("numbers old and new lines across hunks", () => {
    const lines = parsePatch("@@ -3,3 +3,4 @@ def f():\n a\n-b\n+B\n+C\n d\n\\ No newline at end of file\n@@ -20 +21 @@\n-x\n+y\n");
    expect(lines.map((l) => [l.kind, l.oldNo, l.newNo])).toEqual([
      ["hunk", null, null], ["ctx", 3, 3], ["del", 4, null], ["add", null, 4], ["add", null, 5], ["ctx", 5, 6], ["meta", null, null],
      ["hunk", null, null], ["del", 20, null], ["add", null, 21],
    ]);
  });
  it("is empty for an empty patch", () => expect(parsePatch("")).toEqual([]));
});

describe("critic locations", () => {
  it("splits path and line", () => {
    expect(findingTarget("src/api/a.py:42")).toEqual({ path: "src/api/a.py", line: 42 });
    expect(findingTarget("./a.py")).toEqual({ path: "a.py", line: null });
  });
  it("matches a file by full or trailing path, never by partial name", () => {
    expect(findingMatchesFile("src/api/a.py:3", "src/api/a.py")).toBe(true);
    expect(findingMatchesFile("a.py:3", "src/api/a.py")).toBe(true);
    expect(findingMatchesFile("a.py:3", "src/api/data.py")).toBe(false);
    expect(findingMatchesFile("", "src/api/a.py")).toBe(false);
  });
});

describe("replay", () => {
  const at = (seq: number, agent: string, kind: EventKind, body: string, refs: string[] = []): BoardEvent =>
    ({ ...ev(seq, kind, agent, body), ts: `2026-11-17T12:00:${String(seq * 10).padStart(2, "0")}.000000Z`, refs });
  const run: BoardEvent[] = [
    at(1, "coordinator", "plan", "worker api: build the api"),
    at(2, "coordinator", "mention", "@api go"),
    at(3, "swarmd", "update", "api replied: built it", ["reply:api", "cost:0.0200", "turn:1"]),
    at(4, "api", "done", "finished"),
    at(5, "swarmd", "update", "verifying api (round 1)", ["task:api:verifying"]),
    at(6, "swarmd", "update", "verify-api replied: fails", ["reply:verify-api", "cost:0.0100", "turn:1"]),
    at(7, "verify-api", "verify", "fail api: 1. tests fail", ["task:api:failed"]),
    at(8, "verify-api", "verify", "pass api: ok"), // an old event without the tag is read from its body
    at(9, "swarmd", "blocked", "ui failed twice", ["task:ui:escalated"]),
  ];
  const task = (i: number, name: string) => replayState(run, i, ["api", "ui"]).agents.find((a) => a.name === name)?.task;

  it("rebuilds task status step by step", () => {
    expect([1, 3, 4, 6, 7, 8].map((i) => task(i, "api"))).toEqual(["open", "awaiting_verification", "verifying", "failed", "done", "done"]);
    expect(task(0, "api")).toBeUndefined(); // not involved yet
    expect(task(8, "ui")).toBe("escalated");
    expect(task(8, "verify-api")).toBeNull(); // verifiers have no task
  });
  it("sums reported cost, turns and progress up to the cursor", () => {
    const mid = replayState(run, 2, ["api", "ui"]);
    expect(mid.costUsd).toBeCloseTo(0.02);
    expect(mid.agents.find((a) => a.name === "api")).toMatchObject({ turns: 1, events: 1 });
    expect(mid.elapsedS).toBe(20);
    expect(mid.at?.seq).toBe(3);
    const end = replayState(run, 99, ["api", "ui"]); // clamped to the last event
    expect(end.costUsd).toBeCloseTo(0.03);
    expect([end.tasks, end.tasksDone]).toEqual([2, 1]);
    expect(end.agents.map((a) => a.name)).toEqual(["api", "ui", "coordinator", "verify-api"]); // task owners first; swarmd is not an actor
  });
  it("handles an empty board", () => expect(replayState([], 0)).toMatchObject({ at: null, agents: [], costUsd: 0 }));
  it("finds key moments in both directions", () => {
    expect(nextKeyMoment(run, 0, 1)).toBe(3);
    expect(nextKeyMoment(run, 3, 1)).toBe(6);
    expect(nextKeyMoment(run, 6, -1)).toBe(3);
    expect(nextKeyMoment(run, 3, -1)).toBe(-1);
    expect(nextKeyMoment(run, 8, 1)).toBe(-1);
  });
  it("formats durations", () => expect([5, 75, 3725].map(duration)).toEqual(["5s", "1m 15s", "1h 2m"]));
});
