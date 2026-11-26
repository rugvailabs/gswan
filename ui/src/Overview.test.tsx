import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ASK_STATUS } from "./plain";
import { useStore } from "./store";
import type { BoardEvent, ResultInfo, RunState, StatusResponse, WorkerView } from "./types";

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

const ev = (seq: number, agent: string, kind: BoardEvent["kind"], body: string, refs: string[] = []): BoardEvent =>
  ({ seq, id: `evt_${seq}`, swarmId: "swm_1", ts: `2026-11-17T12:00:0${seq % 10}.000000Z`, agent, kind, body, taskId: null, refs });

const w = (name: string, extra: Partial<WorkerView>): WorkerView => ({
  name, status: "idle", busy: false, turns: 1, tokens: { input: 1, output: 1, cached: 0, total: 2 }, owns: [`src/${name}/`], branch: null, model: "sonnet",
  queue: 0, lastActivity: null, activityNote: null, lastError: null, task: { id: `tsk_${name}`, status: "open", failedRounds: 0, reasons: [] }, ...extra,
});

let status: StatusResponse;
let result: ResultInfo | null;
let run: RunState;
let calls: { url: string; init?: RequestInit }[];
let events: BoardEvent[];

const baseResult = (): ResultInfo => ({
  stage: "integration", location: { path: "/home/me/proj/.swarm/wt/integration", branch: "swarm/swm_1/integration", kind: "integration", worker: null },
  workers: [{ name: "login-screen", branch: "swarm/swm_1/login-screen", path: "/wt/login", taskStatus: "done" }, { name: "api", branch: "b", path: "/wt/api", taskStatus: "done" }],
  merged: ["login-screen", "api"], verified: true, applyCommand: "swarm integrate --apply",
  project: { kind: "node", label: "React app (Vite)", runCommand: ["npm", "run", "dev"], installCommand: ["npm", "install"], template: "react-app", cwd: "" },
  files: [{ path: "src/App.tsx", status: "added" }, { path: "package.json", status: "modified" }, { path: "src/old.ts", status: "unchanged" }],
  summary: { added: 1, modified: 1, deleted: 0, total: 3 },
});

beforeEach(() => {
  calls = [];
  FakeES.last = null;
  window.history.replaceState(null, "", "/");
  status = {
    ok: true, limits: { max_workers: 6, max_wakeups: 40, max_wall_s: 3600, max_tokens: 1000000 },
    usage: { workers: 2, wakeups: 3, wall_s: 720, tokens: 300000, tokens_cached: 0 }, allTasksSettled: false, unsettled: [], digest: "", monitor: [],
    workers: [
      w("login-screen", { busy: true, status: "running", owns: ["src/login/", "README.md"] }),
      w("api", { task: { id: "t2", status: "failed", failedRounds: 1, reasons: ["no tests", "500 on login"] } }),
    ],
  };
  result = baseResult();
  run = { running: false, command: null, cwd: null, startedAt: null, exitCode: null, url: null, log: [] };
  events = [
    ev(1, "coordinator", "plan", "worker login-screen: build the login form | owns src/login/"),
    ev(2, "login-screen", "claim", "taking the form"),
    ev(3, "verify-api", "verify", "fail api: 1. no tests | 2. 500 on login", ["task:api:failed"]),
  ];
  vi.stubGlobal("EventSource", FakeES);
  vi.stubGlobal("fetch", vi.fn(async (rawUrl: string, init?: RequestInit) => {
    const url = rawUrl.replace(/^\/api/, "");
    calls.push({ url, init });
    const json = (b: unknown, s = 200) => ({ ok: s < 400, status: s, statusText: "x", json: async () => b }) as Response;
    if (url.startsWith("/swarms/current")) return json({ id: "swm_1", task: "Make a login page", status: "active", createdAt: "x" });
    if (url.startsWith("/status")) return json(status);
    if (url.startsWith("/events?")) return json({ items: events, total: events.length, limit: 500, offset: 0 });
    if (url.startsWith("/result/open")) return json({ detail: "no single location" }, 404);
    if (url === "/result/run" && init?.method === "POST") { run = { ...run, running: true, command: ["npm", "run", "dev"], log: ["$ npm install", "added 10 packages"] }; return json(run); }
    if (url === "/result/run/stop") { run = { ...run, running: false, exitCode: 0 }; return json(run); }
    if (url === "/result/run") return json(run);
    if (url.startsWith("/result")) return result ? json(result) : json({ detail: "Not Found" }, 404);
    if (url.startsWith("/workers/") && url.endsWith("/mentions")) return json({ ok: true }, 202);
    if (url.startsWith("/approvals/")) return json({ ok: true, approval: {} });
    if (url === "/limits") return json({ ok: true });
    return json({ message: "nope" }, 404);
  }));
  useStore.setState({ token: "tok", swarm: null, status: null, events: [], live: false, error: null, result: null, resultLoad: "idle", resultError: null, resultWorker: null });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

const body = (url: string) => JSON.parse(String(calls.filter((c) => c.url === url).at(-1)?.init?.body)) as unknown;

describe("Overview", () => {
  it("is the default tab and switches to Advanced (kept in the URL hash)", async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    const head = await screen.findByRole("region", { name: "what are we building" });
    expect(await within(head).findByText("Make a login page")).toBeInTheDocument();
    expect(within(head).getByLabelText("time working")).toHaveTextContent("12 minutes");
    expect(within(head).getByLabelText("fuel")).toHaveTextContent("30% of the fuel is used");
    expect(screen.queryByText(/tokens/i)).toBeNull(); // no jargon
    await user.click(screen.getByRole("tab", { name: "Advanced" }));
    expect(window.location.hash).toBe("#advanced");
    expect(await screen.findByRole("article", { name: "worker api" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Overview" }));
    expect(window.location.hash).toBe("");
    expect(screen.getByRole("region", { name: "your project" })).toBeInTheDocument();
  });

  it("opens Advanced straight from #advanced", async () => {
    window.location.hash = "#advanced";
    render(<App />);
    expect(screen.getByRole("tab", { name: "Advanced" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("region", { name: "board" })).toBeInTheDocument();
  });

  it("shows parts in plain words and lets you ask or tell a builder something", async () => {
    const user = userEvent.setup();
    render(<App />);
    const card = await screen.findByRole("article", { name: "part Login Screen" });
    expect(within(card).getByText("build the login form")).toBeInTheDocument(); // goal from the plan event
    expect(within(card).getByLabelText("Login Screen status")).toHaveTextContent("Writing code");
    expect(within(within(card).getByRole("list", { name: "Login Screen works on" })).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["▸ src/login", "· README.md"]);
    expect(within(screen.getByRole("article", { name: "part API" })).getByLabelText("API status")).toHaveTextContent("Sent back to fix 2 problems");
    await user.click(within(card).getByRole("button", { name: "What are you doing?" }));
    await waitFor(() => expect(body("/workers/login-screen/mentions")).toEqual({ message: ASK_STATUS, sender: "human" }));
    await user.click(within(card).getByRole("button", { name: "Tell it something" }));
    await user.type(within(card).getByLabelText("Message for Login Screen"), "use blue buttons");
    await user.click(within(card).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(body("/workers/login-screen/mentions")).toEqual({ message: "use blue buttons", sender: "human" }));
    await user.click(within(card).getByRole("button", { name: /^Login Screen/ }));
    expect(within(within(card).getByRole("list", { name: "Login Screen story" })).getByText("Builder Login Screen started working: taking the form")).toBeInTheDocument();
  });

  it("tells the story in plain sentences, newest first, with technical detail on request", async () => {
    const user = userEvent.setup();
    render(<App />);
    const story = await screen.findByRole("region", { name: "story" });
    const items = await within(story).findAllByRole("listitem");
    expect(items[0]).toHaveTextContent("The checker found problems in API and sent it back: no tests");
    expect(within(story).queryByText(/fail api: 1\./)).toBeNull();
    await user.click(within(story).getByLabelText("Show technical detail"));
    expect(within(story).getByText(/verify-api · verify: fail api: 1\. no tests/)).toBeInTheDocument();
    FakeES.last!.emit(ev(4, "monitor", "nudge", "login-screen hung: no output for 300s."));
    expect(await within(story).findByText("Login Screen went quiet, so the team lead gave it a nudge")).toBeInTheDocument();
  });
});

describe("Journey", () => {
  it("shows the stages, marks the current ones, and explains a stage on click and with the keyboard", async () => {
    const user = userEvent.setup();
    result = { ...baseResult(), stage: "workers", location: null, verified: null, applyCommand: null };
    render(<App />);
    const j = await screen.findByRole("region", { name: "progress steps" });
    await waitFor(() => expect(within(j).getByRole("button", { name: "Build: happening now" })).toBeInTheDocument());
    expect(within(j).getByRole("button", { name: "Fix: happening now" })).toHaveAttribute("aria-current", "step");
    expect(within(j).getByRole("button", { name: "Understand: done" })).toBeInTheDocument();
    expect(within(j).getByRole("button", { name: "Ready: still ahead" })).toBeInTheDocument();
    await user.click(within(j).getByRole("button", { name: "Fix: happening now" }));
    const detail = within(j).getByRole("region", { name: "step Fix" });
    expect(detail).toHaveTextContent("go back to their builder");
    expect(within(detail).getByText(/The checker found problems in API/)).toBeInTheDocument();
    within(j).getByRole("button", { name: "Fix: happening now" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(within(j).getByRole("button", { name: /^Combine/ })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(within(j).getByRole("region", { name: "step Combine" })).toHaveTextContent("Nothing has happened in this step yet.");
  });
});

describe("NeedsYou", () => {
  it("is hidden when nothing is needed", async () => {
    render(<App />);
    await screen.findByRole("article", { name: "part API" });
    expect(screen.queryByRole("region", { name: "needs your attention" })).toBeNull();
  });

  it("asks in plain words: approve a command, add fuel, and direct a stuck part", async () => {
    const user = userEvent.setup();
    status.usage!.tokens = 1000000;
    status.pause = { paused: true, reason: "tokens", agents: ["api"] };
    status.pendingApprovals = [{ id: "apr_1", worker: "login-screen", command: "npm install", reason: "`npm` is not allowed", program: "npm", status: "pending", decision: null, note: "", createdAt: "x", decidedAt: null }];
    status.workers[1] = w("api", { task: { id: "t2", status: "escalated", failedRounds: 2, reasons: ["login returns 500"] } });
    render(<App />);
    const box = await screen.findByRole("region", { name: "needs your attention" });
    const ask = within(box).getByRole("article", { name: "permission request from Login Screen" });
    expect(ask).toHaveTextContent("Builder Login Screen wants to run npm install");
    expect(ask).toHaveTextContent("This downloads the libraries the project needs.");
    await user.click(within(ask).getByRole("button", { name: "Always allow npm" }));
    await waitFor(() => expect(body("/approvals/apr_1")).toEqual({ decision: "program", note: "" }));
    await user.click(within(ask).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(body("/approvals/apr_1")).toEqual({ decision: "allow", note: "" }));
    await user.click(within(ask).getByRole("button", { name: "Say no" }));
    await waitFor(() => expect(body("/approvals/apr_1")).toEqual({ decision: "deny", note: "" }));
    await user.click(within(box).getByRole("button", { name: "Give it 50% more and continue" }));
    await waitFor(() => expect(body("/limits")).toEqual({ maxTokens: 1500000 }));
    const stuck = within(box).getByRole("article", { name: "API is stuck" });
    expect(stuck).toHaveTextContent("login returns 500");
    await user.type(within(stuck).getByLabelText("Tell API what to do"), "return 401 instead");
    await user.click(within(stuck).getByRole("button", { name: "Tell it what to do" }));
    await waitFor(() => expect(body("/workers/api/mentions")).toEqual({ message: "return 401 instead", sender: "human" }));
    expect(await within(stuck).findByRole("status")).toHaveTextContent("Sent.");
  });
});

describe("YourProject", () => {
  it("shows where the combined result is, what is in it, and how to add it", async () => {
    const user = userEvent.setup();
    render(<App />);
    const card = await screen.findByRole("region", { name: "your project" });
    expect(await within(card).findByLabelText("result headline")).toHaveTextContent("All parts combined into one copy — checked ✓");
    expect(within(card).getByLabelText("project folder")).toHaveTextContent("/home/me/proj/.swarm/wt/integration");
    expect(within(card).getByText(/Saved on branch/)).toHaveTextContent("swarm/swm_1/integration");
    expect(within(card).getByText("React app (Vite)")).toBeInTheDocument();
    expect(within(card).getByText(/Started from template react-app/)).toBeInTheDocument();
    expect(within(card).getByLabelText("change summary")).toHaveTextContent("1 new file, 1 changed");
    expect(within(card).getByRole("link", { name: "Download zip" })).toHaveAttribute("href", expect.stringMatching(/\/result\/zip\?token=tok$/));
    expect(within(card).getByLabelText("apply command")).toHaveTextContent("swarm integrate --apply");
    await user.click(within(card).getAllByRole("button", { name: "Copy" })[0]!);
    expect(await navigator.clipboard.readText()).toBe("/home/me/proj/.swarm/wt/integration"); // user-event's clipboard stub
    expect(await within(card).findByRole("status")).toHaveTextContent("Copied the folder path.");
    await user.click(within(card).getByRole("button", { name: "Show the files (3)" }));
    const tree = within(card).getByRole("tree", { name: "project files" });
    expect(within(tree).getByRole("treeitem", { name: "package.json" })).toHaveTextContent("changed");
    const src = within(tree).getByRole("treeitem", { name: "src/" });
    expect(src).toHaveAttribute("aria-expanded", "true");
    expect(within(src).getByRole("treeitem", { name: "src/App.tsx" })).toHaveTextContent("new");
    await user.click(within(src).getByRole("button", { name: /src\// }));
    expect(within(tree).queryByRole("treeitem", { name: "src/App.tsx" })).toBeNull();
  });

  it("explains 'pick a part first' when there is no single copy to open", async () => {
    const user = userEvent.setup();
    render(<App />);
    const card = await screen.findByRole("region", { name: "your project" });
    await user.click(await within(card).findByRole("button", { name: "Open folder" }));
    expect(await within(card).findByRole("status")).toHaveTextContent("Pick a part first");
    expect(body("/result/open")).toEqual({ target: "folder" });
  });

  it("asks before running, then shows the live log, the app link and Stop", async () => {
    const user = userEvent.setup();
    render(<App />);
    const card = await screen.findByRole("region", { name: "your project" });
    await user.click(await within(card).findByRole("button", { name: "▶ Run it" }));
    const dialog = screen.getByRole("dialog", { name: "Run the project?" });
    expect(dialog).toHaveTextContent("This will run the code the agents wrote on your computer");
    expect(within(dialog).getByRole("checkbox")).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.some((c) => c.url === "/result/run" && c.init?.method === "POST")).toBe(false);
    await user.click(within(card).getByRole("button", { name: "▶ Run it" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Yes, run it" }));
    await waitFor(() => expect(body("/result/run")).toEqual({ confirm: true, install: true }));
    const panelEl = await within(card).findByRole("region", { name: "running project" });
    expect(within(panelEl).getByLabelText("run log")).toHaveTextContent("$ npm install");
    run = { ...run, url: "http://localhost:5173", log: [...run.log, "Local: http://localhost:5173"] };
    expect(await within(panelEl).findByRole("link", { name: "Open the app" }, { timeout: 3000 })).toHaveAttribute("href", "http://localhost:5173");
    await user.click(within(panelEl).getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(within(panelEl).getByLabelText("run status")).toHaveTextContent("Stopped"));
  });

  it("lets you pick a part when each part is in its own draft copy", async () => {
    const user = userEvent.setup();
    result = { ...baseResult(), stage: "workers", location: null, verified: null, applyCommand: null };
    render(<App />);
    const card = await screen.findByRole("region", { name: "your project" });
    expect(await within(card).findByText("Each part is in its own draft copy")).toBeInTheDocument();
    result = { ...baseResult(), stage: "workers", location: { path: "/wt/api", branch: "b", kind: "worker", worker: "api" }, verified: true, applyCommand: null };
    await user.selectOptions(within(card).getByLabelText("which part to show"), "api");
    await waitFor(() => expect(calls.some((c) => c.url === "/result?worker=api")).toBe(true));
    expect(await within(card).findByLabelText("project folder")).toHaveTextContent("/wt/api");
    expect(within(card).getByRole("link", { name: "Download zip" })).toHaveAttribute("href", expect.stringContaining("worker=api"));
  });

  it("shows other stages: nothing yet, and already added", async () => {
    result = { ...baseResult(), stage: "none", location: null, files: [], summary: { added: 0, modified: 0, deleted: 0, total: 0 } };
    render(<App />);
    const card = await screen.findByRole("region", { name: "your project" });
    expect(await within(card).findByText("Nothing built yet")).toBeInTheDocument();
    result = { ...baseResult(), stage: "applied", applyCommand: null, location: { path: "/home/me/proj", branch: null, kind: "repo", worker: null } };
    await useStore.getState().loadResult();
    expect(await within(card).findByText("Already added to your project")).toBeInTheDocument();
    expect(within(card).queryByLabelText("apply command")).toBeNull();
  });

  it("says when the server cannot tell where the result is (404)", async () => {
    result = null;
    render(<App />);
    const card = await screen.findByRole("region", { name: "your project" });
    expect(await within(card).findByText("Result info unavailable")).toBeInTheDocument();
  });

  it("reloads the result when a check lands", async () => {
    render(<App />);
    const card = await screen.findByRole("region", { name: "your project" });
    await within(card).findByLabelText("result headline");
    await waitFor(() => expect(FakeES.last).not.toBeNull());
    const before = calls.filter((c) => c.url === "/result").length;
    FakeES.last!.emit(ev(5, "verify-api", "verify", "pass api: ok", ["task:api:done"]));
    await waitFor(() => expect(calls.filter((c) => c.url === "/result").length).toBeGreaterThan(before));
  });
});
