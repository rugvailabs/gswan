import { describe, expect, it } from "vitest";
import { budgetParts, eventTone, filterEvents, mergeEvents, summarize, timeAgo } from "./lib";
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
