import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { useStore } from "./store";
import type { BoardEvent, StatusResponse } from "./types";

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
  ok: true, limits: { max_workers: 6, max_wakeups: 40, max_wall_s: 3600, max_cost_usd: 10 },
  usage: { workers: 2, wakeups: 3, wall_s: 10, cost_usd: 0.032 }, allTasksSettled: false, unsettled: ["api"], digest: "", monitor: [{ worker: "api", state: "hung", detail: "no output for 20s" }],
  workers: [
    { name: "api", status: "running", busy: true, turns: 1, costUsd: 0.0123, owns: ["src/api"], branch: "swarm/swm_1/api", model: "haiku", queue: 1,
      lastActivity: new Date().toISOString(), activityNote: "tool:Bash", lastError: null, task: { id: "tsk_1", status: "verifying", failedRounds: 1, reasons: [] } },
    { name: "ui", status: "idle", busy: false, turns: 2, costUsd: 0.02, owns: ["src/ui"], branch: null, model: "sonnet", queue: 0,
      lastActivity: null, activityNote: null, lastError: null, task: { id: "tsk_2", status: "done", failedRounds: 0, reasons: [] } },
  ],
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
    if (url.startsWith("/workers/api/mentions")) return json({ ok: true, started: "api" }, 202);
    if (url === "/events") return json(ev(9, "human", "human", JSON.parse(String(init?.body)).body), 201);
    return json({ message: "nope" }, 404);
  }));
  useStore.setState({ token: "tok", swarm: null, status: null, events: [], live: false, error: null, kinds: new Set(), agent: null, mentionTarget: null });
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
    expect(screen.getByLabelText("total cost")).toHaveTextContent("$0.032");
    expect(screen.getByLabelText("budget")).toHaveTextContent("wake-ups 3/40");
    expect(screen.getByLabelText("budget")).toHaveTextContent("spend $0.03/$10");
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
});
