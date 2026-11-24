import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { useStore } from "./store";
import type { BoardEvent, StatusResponse, WorkerDiff } from "./types";

class FakeES {
  static last: FakeES | null = null;
  listeners: Record<string, (m: MessageEvent<string>) => void> = {};
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeES.last = this; queueMicrotask(() => this.onopen?.()); }
  addEventListener(t: string, f: (m: MessageEvent<string>) => void) { this.listeners[t] = f; }
  emit(e: BoardEvent) { this.listeners["board"]?.({ data: JSON.stringify(e) } as MessageEvent<string>); }
  close() {}
}

const ev = (seq: number, agent: string, kind: BoardEvent["kind"], body: string): BoardEvent =>
  ({ seq, id: `evt_${seq}`, swarmId: "swm_1", ts: "2026-11-17T12:00:0" + seq + ".000000Z", agent, kind, body, taskId: null, refs: [] });

const status: StatusResponse = {
  ok: true, limits: { max_workers: 6, max_wakeups: 40, max_wall_s: 3600, max_tokens: 3000000 },
  usage: { workers: 2, wakeups: 3, wall_s: 10, tokens: 131500, tokens_cached: 1200000 }, allTasksSettled: false, unsettled: ["api"], digest: "", monitor: [{ worker: "api", state: "hung", detail: "no output for 20s" }],
  workers: [
    { name: "api", status: "running", busy: true, turns: 1, tokens: { input: 84000, output: 2500, cached: 1200000, total: 86500 }, owns: ["src/api"], branch: "swarm/swm_1/api", model: "haiku", queue: 1,
      lastActivity: new Date().toISOString(), activityNote: "tool:Bash", lastError: null, task: { id: "tsk_1", status: "verifying", failedRounds: 1, reasons: [] } },
    { name: "ui", status: "idle", busy: false, turns: 2, tokens: { input: 44100, output: 900, cached: 0, total: 45000 }, owns: ["src/ui"], branch: null, model: "sonnet", queue: 0,
      lastActivity: null, activityNote: null, lastError: null, task: { id: "tsk_2", status: "done", failedRounds: 0, reasons: [] } },
  ],
};

const diff: WorkerDiff = {
  worker: "api", role: "worker", goal: "build the api", owns: ["src/api"], branch: "swarm/swm_1/api", base: "abcdef1234567890",
  task: { id: "tsk_1", status: "failed", failedRounds: 1, reasons: ["tests fail"] }, moreFiles: 0, note: null, uncommitted: ["scratch.txt"],
  totals: { files: 2, additions: 3, deletions: 1 },
  files: [
    { path: "src/api/a.py", status: "M", additions: 2, deletions: 1, binary: false, outOfScope: false, redacted: false, truncated: false, patch: "@@ -1,2 +1,3 @@\n import db\n-old()\n+q = build(user)\n+db.run(q)\n" },
    { path: "docs/oops.md", status: "A", additions: 1, deletions: 0, binary: false, outOfScope: true, redacted: false, truncated: false, patch: "@@ -0,0 +1 @@\n+hello\n" },
  ],
  critiques: [
    { critic: "critic-api-security", round: 1, severity: "blocker", location: "src/api/a.py:2", reason: "SQL injection" },
    { critic: "critic-api-tests", round: 1, severity: "nit", location: "", reason: "no test for the error path" },
  ],
  verdicts: [{ seq: 7, passed: false, by: "verify-api", text: "1. tests fail" }],
};

let calls: { url: string; init?: RequestInit }[];
beforeEach(() => {
  calls = [];
  FakeES.last = null;
  vi.stubGlobal("EventSource", FakeES);
  vi.stubGlobal("fetch", vi.fn(async (rawUrl: string, init?: RequestInit) => {
    const url = rawUrl.replace(/^\/api/, ""); // dev builds prefix API calls with /api (vite proxy)
    calls.push({ url, init });
    const json = (b: unknown, s = 200) => ({ ok: s < 400, status: s, statusText: "x", json: async () => b }) as Response;
    if (url.startsWith("/swarms/current")) return json({ id: "swm_1", task: "build the thing", status: "active", createdAt: "x" });
    if (url.startsWith("/status")) return json(status);
    if (url.startsWith("/events?")) return json({ items: [ev(1, "coordinator", "plan", "split work"), ev(2, "api", "claim", "taking api")], total: 2, limit: 500, offset: 0 });
    if (url === "/workers" && init?.method === "POST") return json({ ok: true, worker: "docs" }, 201);
    if (url === "/limits" && init?.method === "POST") return json({ ok: true }, 200);
    if (url.startsWith("/approvals/") && init?.method === "POST") return json({ ok: true, approval: {} }, 200);
    if (url === "/workers/ui/model" && init?.method === "POST") return json({ ok: true, from: "sonnet", to: "haiku" }, 200);
    if (url === "/workers/api/diff") return json(diff);
    if (url === "/workers/ui/diff") return json({ message: "no worker named 'ui' in the active swarm" }, 404);
    if (url.startsWith("/workers/api/mentions")) return json({ ok: true, started: "api" }, 202);
    if (url === "/events") return json(ev(9, "human", "human", JSON.parse(String(init?.body)).body), 201);
    return json({ message: "nope" }, 404);
  }));
  useStore.setState({ token: "tok", swarm: null, status: null, events: [], live: false, error: null, kinds: new Set(), agent: null, mentionTarget: null, diffWorker: null, view: "board" });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("App", () => {
  it("renders swarm header, worker cards, board and live state", async () => {
    render(<App />);
    expect(await screen.findByText("build the thing")).toBeInTheDocument();
    const api = await screen.findByRole("article", { name: "worker api" });
    expect(within(api).getByText(/verifying/)).toBeInTheDocument();
    expect(within(api).getByText(/1 failed round/)).toBeInTheDocument();
    expect(within(api).getByText(/monitor: hung/)).toBeInTheDocument();
    expect(within(api).getByText(/tool:Bash/)).toBeInTheDocument();
    expect(screen.getByLabelText("tasks done")).toHaveTextContent("1/2 tasks done");
    expect(screen.getByLabelText("total tokens")).toHaveTextContent("131.5k tokens");
    expect(screen.getByLabelText("api tokens")).toHaveTextContent("84k in · 2.5k out");
    expect(within(api).getByText("1.20M")).toBeInTheDocument(); // cached, shown apart
    expect(screen.queryByText(/\$\d/)).not.toBeInTheDocument(); // no prices anywhere
    expect(screen.getByLabelText("budget")).toHaveTextContent("wake-ups 3/40");
    expect(screen.getByLabelText("budget")).toHaveTextContent("tokens 131.5k/3M");
    const board = screen.getByRole("region", { name: "board" });
    expect(within(board).getByText("split work")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("connection")).toHaveTextContent("live"));
    expect(calls.every((c) => (c.init?.headers as Record<string, string>)["Authorization"] === "Bearer tok")).toBe(true);
  });

  it("shows events pushed over the stream and passes the token + resume seq", async () => {
    render(<App />);
    await screen.findByText("taking api");
    await waitFor(() => expect(FakeES.last).not.toBeNull());
    expect(FakeES.last!.url).toContain("sinceSeq=2");
    expect(FakeES.last!.url).toContain("token=tok");
    FakeES.last!.emit(ev(3, "api", "blocked", "need the schema"));
    FakeES.last!.emit(ev(3, "api", "blocked", "need the schema")); // duplicate is ignored
    expect(await screen.findAllByText("need the schema")).toHaveLength(1);
  });

  it("filters the board by kind", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("taking api");
    await user.click(screen.getByRole("button", { name: "plan" }));
    const board = screen.getByRole("region", { name: "board" });
    expect(within(board).getByText("split work")).toBeInTheDocument();
    expect(within(board).queryByText("taking api")).not.toBeInTheDocument();
  });

  it("sends a mention to the chosen worker and clears the draft", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("article", { name: "worker api" });
    await user.click(within(screen.getByRole("article", { name: "worker api" })).getByRole("button", { name: "@ mention" }));
    await user.type(screen.getByLabelText("Message"), "please add tests");
    await user.click(screen.getByRole("button", { name: /Send to @api/ }));
    await waitFor(() => expect(calls.some((c) => c.url === "/workers/api/mentions")).toBe(true));
    const sent = calls.find((c) => c.url === "/workers/api/mentions")!;
    expect(JSON.parse(String(sent.init?.body))).toEqual({ message: "please add tests", sender: "human" });
    await waitFor(() => expect(screen.getByLabelText("Message")).toHaveValue(""));
  });

  it("posts a board note and shows it immediately", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("taking api");
    await user.click(screen.getByRole("tab", { name: "Note" }));
    await user.type(screen.getByLabelText("Note"), "freeze the API shape");
    await user.click(screen.getByRole("button", { name: /Post note/ }));
    expect(await within(screen.getByRole("region", { name: "board" })).findByText("freeze the API shape")).toBeInTheDocument();
  });

  it("shows a helpful error when there is no swarm", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 409, statusText: "Conflict", json: async () => ({ message: "no active swarm; run swarm init" }) }) as Response));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/no active swarm/);
    expect(screen.getByRole("alert")).toHaveTextContent(/swarm init/);
  });

  it("shows an agent's conversation: mentions to it and its own replies", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("taking api");
    useStore.getState().addEvents([
      ev(3, "coordinator", "mention", "@api build the endpoint"),
      { ...ev(4, "swarmd", "update", "api replied: endpoint done"), refs: ["reply:api"] },
    ]);
    await user.click(screen.getByRole("tab", { name: /Conversations/ }));
    await user.click(within(screen.getByRole("list", { name: "agents" })).getByRole("button", { name: /^api/ }));
    const thread = screen.getByRole("list", { name: "thread" });
    expect(within(thread).getByText("@api build the endpoint")).toBeInTheDocument();
    expect(within(thread).getByText("api replied: endpoint done")).toBeInTheDocument();
    expect(within(thread).getByText("reply")).toBeInTheDocument();
  });

  it("shows a worker's diff with scope drift, verdicts and critic findings on the right file", async () => {
    const user = userEvent.setup();
    render(<App />);
    const card = await screen.findByRole("article", { name: "worker api" });
    await user.click(within(card).getByRole("button", { name: "diff" }));
    const panel = await screen.findByRole("region", { name: "diff api" });
    const summary = await within(panel).findByLabelText("diff summary");
    expect(summary).toHaveTextContent("2 files changed");
    expect(summary).toHaveTextContent("+3");
    expect(summary).toHaveTextContent("1 outside owned paths (src/api)");
    expect(within(panel).getByText(/scratch.txt/)).toBeInTheDocument();
    expect(within(within(panel).getByRole("list", { name: "verdicts" })).getByText("fail")).toBeInTheDocument();
    expect(within(within(panel).getByRole("list", { name: "findings for src/api/a.py" })).getByText(/SQL injection/)).toBeInTheDocument();
    expect(within(within(panel).getByRole("list", { name: "general findings" })).getByText(/no test for the error path/)).toBeInTheDocument();
    const patch = within(panel).getByLabelText("patch src/api/a.py");
    const added = within(patch).getByText("+q = build(user)").closest(".diff-line")!;
    expect(added).toHaveClass("diff-add", "diff-cited"); // the line the critic pointed at (new line 2)
    expect(within(patch).getByText("-old()").closest(".diff-line")).toHaveClass("diff-del");
    expect(within(panel).getByText("outside owned paths")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region", { name: "diff api" })).not.toBeInTheDocument();
  });

  it("replays the run: stepping back shows the swarm as it stood at that event", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("taking api");
    useStore.getState().addEvents([
      ev(3, "api", "done", "built the endpoint"),
      { ...ev(4, "verify-api", "verify", "pass api: tests green"), refs: ["task:api:done"] },
    ]);
    await user.click(screen.getByRole("tab", { name: "Replay" }));
    const pos = screen.getByLabelText("replay position");
    const state = screen.getByRole("list", { name: "state at this moment" });
    const list = screen.getByRole("list", { name: "replay events" });
    expect(pos).toHaveTextContent("Event 4 of 4");
    expect(pos).toHaveTextContent("1/1 tasks done"); // ui has not appeared on the board yet
    expect(within(state).getByText("api").closest("li")).toHaveTextContent(/^api\s*done/);
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(pos).toHaveTextContent("Event 3 of 4");
    expect(pos).toHaveTextContent("0/1 tasks done");
    expect(within(state).getByText("awaiting verification")).toBeInTheDocument();
    expect(within(list).queryByText("pass api: tests green")).not.toBeInTheDocument();
    expect(within(list).getByText("built the endpoint").closest("li")).toHaveAttribute("aria-current", "step");
    await user.click(screen.getByRole("button", { name: "First" }));
    expect(pos).toHaveTextContent("Event 1 of 4");
    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Key moment ▶/ }));
    expect(pos).toHaveTextContent("Event 3 of 4");
    await user.click(screen.getByRole("button", { name: "Latest" }));
    expect(screen.getByRole("button", { name: "Play from start" })).toBeInTheDocument();
  });

  it("shows an error when a diff cannot be loaded", async () => {
    const user = userEvent.setup();
    render(<App />);
    const card = await screen.findByRole("article", { name: "worker ui" });
    await user.click(within(card).getByRole("button", { name: "diff" }));
    expect(await within(await screen.findByRole("region", { name: "diff ui" })).findByRole("alert")).toHaveTextContent(/no worker named/);
  });

  it("adds a worker and raises limits from the side panel", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("taking api");
    await user.click(screen.getByRole("tab", { name: "+ Worker" }));
    await user.type(screen.getByLabelText(/^Name/), "docs");
    await user.type(screen.getByLabelText(/^Goal/), "write docs");
    await user.type(screen.getByLabelText(/^Owns/), "docs/, README.md");
    await user.click(screen.getByRole("button", { name: "Add worker" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/workers" && c.init?.method === "POST")).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.url === "/workers")?.init?.body));
    expect(body).toMatchObject({ name: "docs", owns: ["docs/", "README.md"], model: "sonnet" });
    await user.click(screen.getByRole("tab", { name: "Budget" }));
    await user.type(screen.getByLabelText(/Max workers/), "12");
    await user.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(JSON.parse(String(calls.find((c) => c.url === "/limits")?.init?.body))).toEqual({ maxWorkers: 12 }));
  });

  it("asks the human: approve a denied command, and resume a paused swarm", async () => {
    const saved = { ...status, usage: { ...status.usage! } };
    status.usage!.wakeups = 40;
    status.pause = { paused: true, reason: "swarm budget exhausted: wake-ups 40/40", agents: ["api"] };
    status.pendingApprovals = [{ id: "apr_1", worker: "api", command: "npm test", reason: "`npm` is not allowed. Granted commands: none", program: "npm",
      status: "pending", decision: null, note: "", createdAt: "x", decidedAt: null }];
    try {
      const user = userEvent.setup();
      render(<App />);
      const q = await screen.findByRole("region", { name: "needs you" });
      expect(within(q).getByText("npm test")).toBeTruthy();
      expect(within(q).getByRole("alert", { name: "paused" }).textContent).toMatch(/wake-ups 40\/40/);
      await user.type(within(q).getByLabelText("note for the worker"), "ok for tests");
      await user.click(within(q).getByRole("button", { name: "Allow all npm commands" }));
      await waitFor(() => expect(calls.some((c) => c.url === "/approvals/apr_1")).toBe(true));
      expect(JSON.parse(String(calls.find((c) => c.url === "/approvals/apr_1")?.init?.body))).toEqual({ decision: "program", note: "ok for tests" });
      await user.click(within(q).getByRole("button", { name: "Raise by 50% and resume" }));
      await waitFor(() => expect(JSON.parse(String(calls.find((c) => c.url === "/limits")?.init?.body))).toEqual({ maxWakeups: 60 }));
    } finally {
      Object.assign(status, saved); delete status.pause; delete status.pendingApprovals;
    }
  });

  it("switches an idle worker's model, offers groups and @all, and draws who messaged whom", async () => {
    status.workers[0]!.group = "backend"; status.workers[1]!.group = "backend";
    try {
      const user = userEvent.setup();
      render(<App />);
      await screen.findByText("taking api");
      expect((screen.getByLabelText("api model") as HTMLSelectElement).disabled).toBe(true); // running: locked
      await user.selectOptions(screen.getByLabelText("ui model"), "haiku");
      await waitFor(() => expect(JSON.parse(String(calls.find((c) => c.url === "/workers/ui/model")?.init?.body))).toEqual({ model: "haiku" }));
      const send = screen.getByRole("form", { name: "send" });
      expect(within(send).getByRole("option", { name: "@backend (group: api, ui)" })).toBeTruthy();
      expect(within(send).getByRole("option", { name: "@all (every worker)" })).toBeTruthy();
      FakeES.last!.emit({ ...ev(5, "coordinator", "mention", "@backend start"), refs: ["to:api", "to:ui"] });
      FakeES.last!.emit({ ...ev(6, "api", "mention", "@ui need the types"), refs: ["to:ui"] });
      await user.click(screen.getByRole("tab", { name: "Graph" }));
      const table = await screen.findByRole("table", { name: "messages between agents" });
      expect(within(table).getAllByRole("row").map((r) => r.textContent)).toEqual(["fromtomessages", "apiui1", "coordinatorapi1", "coordinatorui1"]);
      expect(screen.getByRole("img", { name: "who messaged whom" })).toBeTruthy();
      await user.click(screen.getByRole("tab", { name: "Graph" }));
      const slider = screen.getByLabelText("graph position");
      fireEvent.change(slider, { target: { value: "0" } }); // back to the first message
      expect(screen.getByRole("button", { name: "go live" })).toBeTruthy();
      await waitFor(() => expect(within(screen.getByRole("table", { name: "messages between agents" })).getAllByRole("row")).toHaveLength(3));
    } finally {
      delete status.workers[0]!.group; delete status.workers[1]!.group;
    }
  });

  it("shows nothing extra when no human is needed", async () => {
    render(<App />);
    await screen.findByText("taking api");
    expect(screen.queryByRole("region", { name: "needs you" })).toBeNull();
  });
});
