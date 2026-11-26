import { describe, expect, it } from "vitest";
import {
  aboutPart, buildTree, buildersOf, describeCommand, explainEvent, fuel, goalsFromEvents, humanizeCommand, journey, overallProgress,
  ownedChip, partName, plainDuration, resultHeadline, roleName, stageOf, workerStory, type StageKey,
} from "./plain";
import type { BoardEvent, EventKind, TaskStatus, WorkerView } from "./types";

const ev = (kind: EventKind, agent: string, body: string, refs: string[] = [], seq = 1): BoardEvent =>
  ({ seq, id: `evt_${seq}`, swarmId: "swm_1", ts: "2026-11-17T12:00:00.000000Z", agent, kind, body, taskId: null, refs });

const worker = (name: string, task: TaskStatus | null = "open", extra: Partial<WorkerView> = {}): WorkerView => ({
  name, status: "idle", busy: false, turns: 0, tokens: { input: 0, output: 0, cached: 0, total: 0 }, owns: task ? [`src/${name}/`] : [], branch: null,
  model: "sonnet", queue: 0, lastActivity: null, activityNote: null, lastError: null,
  task: task ? { id: `tsk_${name}`, status: task, failedRounds: 0, reasons: [] } : null, ...extra,
});

describe("names", () => {
  it("title-cases part names and keeps acronyms", () => {
    expect(partName("auth-api")).toBe("Auth API");
    expect(partName("login_screen")).toBe("Login Screen");
    expect(partName("ui")).toBe("UI");
  });
  it("gives every agent a friendly role", () => {
    expect(roleName("auth-api")).toBe("Builder Auth API");
    expect(roleName("verify-auth-api")).toBe("Checker");
    expect(roleName("critic-auth-api-security")).toBe("Reviewer");
    expect(roleName("plan-critic")).toBe("Plan reviewer");
    expect(roleName("integrator")).toBe("Combiner");
    expect(roleName("coordinator")).toBe("Team lead");
    expect(roleName("monitor")).toBe("Team lead");
    expect(roleName("human")).toBe("You");
    expect(roleName("swarmd")).toBe("The system");
    expect(roleName("scout", new Set(["scout"]))).toBe("Researcher Scout");
  });
});

describe("explainEvent", () => {
  const ws = [worker("login-screen"), worker("scout", null)];
  const x = (kind: EventKind, agent: string, body: string, refs: string[] = []) => explainEvent(ev(kind, agent, body, refs), ws);

  it("plan", () => {
    expect(x("plan", "coordinator", "worker login-screen: build the login form | owns src/login/").text).toBe("The team lead planned a part, Login Screen: build the login form");
    expect(x("plan", "coordinator", "researcher scout: find a date library").text).toMatch(/asked a researcher to look into: find a date library/);
    expect(x("plan", "coordinator", "two parts").text).toBe("Team lead shared the plan: two parts");
  });
  it("claim", () => {
    expect(x("claim", "login-screen", "taking the form")).toMatchObject({ text: "Builder Login Screen started working: taking the form", tone: "info" });
  });
  it("update: replies, checks, reviewers, combining, sleep, denials, fallback", () => {
    expect(x("update", "swarmd", "login-screen replied: form is done", ["reply:login-screen", "tokens:1:2:3"]).text).toBe("Builder Login Screen said: form is done");
    expect(x("update", "swarmd", "verifying login-screen (round 2)").text).toBe("A checker is now testing Login Screen (try 2)");
    expect(x("update", "swarmd", "verifier for login-screen runs on haiku: small").text).toBe("A checker was picked for Login Screen");
    expect(x("update", "swarmd", "critics reviewing login-screen (round 1): security").text).toMatch(/Reviewers are reading/);
    expect(x("update", "swarmd", "critics skipped for login-screen: docs only; the verifier still checks it").text).toMatch(/straight to the checker/);
    expect(x("update", "swarmd", "2 critic blocker(s) on login-screen (round 1); sent back to the worker")).toMatchObject({ tone: "bad", text: "Reviewers found 2 serious problems in Login Screen and sent it back" });
    expect(x("update", "swarmd", "1 critic major(s) on login-screen; sent back once").text).toBe("Reviewers asked Login Screen to fix or explain 1 thing");
    expect(x("update", "swarmd", "merged auth-api into swarm/x/integration")).toMatchObject({ tone: "good", text: expect.stringContaining("into the combined copy") });
    expect(x("update", "swarmd", "merge conflict in a.py (api); integrator resolving").tone).toBe("warn");
    expect(x("update", "swarmd", "login-screen paused before verification; it resumes when a human raises the budget").tone).toBe("warn");
    expect(x("update", "swarmd", "budget allows work again; resuming verification of api").tone).toBe("good");
    expect(x("update", "swarmd", "worker api turn was terminated (monitor/user); it will be resumed").text).toMatch(/will start again/);
    expect(x("update", "swarmd", "api's turn was cut off because the computer slept; resuming it (1/2)").text).toMatch(/went to sleep/);
    expect(x("update", "monitor", "machine slept for 300s; that time does not count").text).toMatch(/asleep/);
    expect(x("update", "swarmd", "worker api was denied: rm -rf /").text).toMatch(/not allowed/);
    expect(x("update", "monitor", "api quiet for 90s but judged still progressing (x); extending").text).toMatch(/still making progress/);
    expect(x("update", "login-screen", "half way").text).toBe("Builder Login Screen: half way");
  });
  it("blocked", () => {
    expect(x("blocked", "swarmd", "paused: swarm budget exhausted: tokens").tone).toBe("warn");
    expect(x("blocked", "swarmd", "login-screen failed verification 2 times; needs a human. Last reasons: a")).toMatchObject({ tone: "bad", text: "Login Screen failed the check 2 times and needs your help" });
    expect(x("blocked", "swarmd", "verifier for api gave no verdict (x); needs a human").text).toMatch(/could not decide about API/);
    expect(x("blocked", "monitor", "api hung and the monitor ladder is exhausted: quiet. Needs a human.").text).toBe("API is stuck and needs you");
    expect(x("blocked", "swarmd", "worker api turn failed: boom").text).toBe("API ran into an error: boom");
    expect(x("blocked", "login-screen", "need the schema")).toMatchObject({ tone: "warn", text: "Builder Login Screen is stuck: need the schema" });
  });
  it("done", () => {
    expect(x("done", "login-screen", "built it")).toMatchObject({ tone: "good", text: "Builder Login Screen says it is finished. Next, a checker looks at it." });
    expect(x("done", "integrator", "Integration branch assembled").text).toMatch(/All parts were put together/);
  });
  it("verify pass and fail (first reason only, raw kept as detail)", () => {
    expect(x("verify", "verify-login-screen", "pass login-screen: tests green")).toMatchObject({ tone: "good", text: "The checker approved Login Screen" });
    const f = x("verify", "verify-login-screen", "fail login-screen: 1. button does nothing | 2. no tests");
    expect(f).toMatchObject({ tone: "bad", text: "The checker found problems in Login Screen and sent it back: button does nothing", detail: "fail login-screen: 1. button does nothing | 2. no tests" });
    expect(x("verify", "verify-integrator", "pass integrator: all good").text).toBe("The checker approved the combined project");
  });
  it("nudge", () => {
    expect(x("nudge", "monitor", "login-screen hung: no output for 300s. If it does not recover it will be restarted.").text).toBe("Login Screen went quiet, so the team lead gave it a nudge");
    expect(x("nudge", "monitor", "api looping: same tool 5 times").text).toMatch(/kept repeating itself/);
    expect(x("nudge", "monitor", "api forgot to report; reminded (1/2).").text).toMatch(/reminded it/);
    expect(x("nudge", "monitor", "restarting api (hung: quiet)").text).toBe("API was stuck, so the team lead restarted it");
  });
  it("mention", () => {
    expect(x("mention", "human", "@login-screen use blue", ["to:login-screen"]).text).toBe("You asked Builder Login Screen: use blue");
    expect(x("mention", "coordinator", "@all wrap up", ["to:a", "to:b"]).text).toBe("Team lead wrote to Builder A and Builder B: wrap up");
    expect(x("mention", "coordinator", "@all wrap up").text).toBe("Team lead wrote to everyone: wrap up");
  });
  it("human notes and project runs", () => {
    expect(x("human", "human", "freeze the API").text).toBe("You left a note for the team: freeze the API");
    expect(x("human", "human", "Ran the project: npm run dev (in .)").text).toBe("You started the project on your computer");
  });
  it("research", () => {
    expect(x("research", "scout", "dayjs is small https://x").text).toBe("Researcher Scout found: dayjs is small https://x");
  });
  it("critique", () => {
    expect(x("critique", "critic-api-security", "blocker api @ src/a.py:2: SQL injection")).toMatchObject({ tone: "bad", text: "A reviewer found a serious problem in API: SQL injection" });
    expect(x("critique", "critic-api-tests", "major api @ src/a.py:9: no error test").tone).toBe("warn");
    expect(x("critique", "critic-api-tests", "nit api: rename x").text).toMatch(/small suggestion/);
    expect(x("critique", "critic-api-tests", "ok api: looks fine").tone).toBe("good");
    expect(x("critique", "plan-critic", "blocker plan: overlapping ownership").text).toBe("The plan reviewer found a serious problem in the plan: overlapping ownership");
  });
  it("decision", () => {
    expect(x("decision", "coordinator", "[split] one worker is enough for this").text).toBe("Team lead made a decision: one worker is enough for this");
  });
  it("approval: asked, allowed, granted, denied", () => {
    expect(x("approval", "swarmd", "apr_1 api asks to run: npm install (denied: `npm` is not allowed)")).toMatchObject({
      tone: "warn", text: "Builder API wants to run `npm install` — downloads the libraries the project needs. It needs your OK." });
    expect(x("approval", "human", "apr_1 human allowed this command for api: npm install").text).toBe("You allowed Builder API to run `npm install`");
    expect(x("approval", "human", "apr_1 human granted `npm` (all its commands) for api: npm install — ok").text).toBe("You let Builder API use `npm` from now on");
    expect(x("approval", "human", "apr_1 human denied it for api: rm -rf x")).toMatchObject({ tone: "bad", text: "You said no to Builder API running `rm -rf x`" });
  });
});

describe("journey", () => {
  const states = (st: Parameters<typeof journey>[0], events: BoardEvent[] = [], result: Parameters<typeof journey>[2] = null) =>
    Object.fromEntries(journey(st, events, result).map((s) => [s.key, s.state])) as Record<StageKey, string>;
  const st = (workers: WorkerView[], allTasksSettled = false) => ({ workers, allTasksSettled });

  it("everything is upcoming without a run", () => {
    expect(Object.values(states(null)).every((s) => s === "upcoming")).toBe(true);
  });
  it("Understand while there are no builders (researchers only)", () => {
    expect(states(st([worker("scout", null, { busy: true })]))).toMatchObject({ understand: "active", plan: "upcoming" });
  });
  it("Plan when builders are defined but none ran, or the plan reviewer is busy", () => {
    expect(states(st([worker("api"), worker("ui")]))).toMatchObject({ understand: "done", plan: "active", build: "upcoming" });
    expect(states(st([worker("api", "open", { turns: 1 }), worker("plan-critic", null, { busy: true })]))).toMatchObject({ plan: "active", build: "active" });
  });
  it("Build when a builder is running with an open task", () => {
    expect(states(st([worker("api", "open", { busy: true, status: "running" }), worker("ui")]))).toMatchObject({ understand: "done", plan: "done", build: "active", check: "upcoming", fix: "upcoming" });
  });
  it("Check when a part waits for or is being checked; Fix skipped when nothing failed", () => {
    const s = states(st([worker("api", "verifying"), worker("ui", "done")]));
    expect(s).toMatchObject({ build: "done", check: "active", fix: "upcoming" });
  });
  it("Fix when a part is sent back", () => {
    expect(states(st([worker("api", "failed"), worker("ui", "open", { busy: true })]))).toMatchObject({ build: "active", check: "done", fix: "active" });
  });
  it("Combine when the integrator exists or the result is an unverified integration", () => {
    const s = states(st([worker("api", "done"), worker("integrator", "open", { owns: [], busy: true })]), [], { stage: "integration", verified: null });
    expect(s).toMatchObject({ check: "done", fix: "skipped", combine: "active", ready: "upcoming" });
    expect(states(st([worker("api", "done")]), [], { stage: "integration", verified: null }).combine).toBe("active");
  });
  it("Fix is done (not skipped) later when something failed before", () => {
    const s = states(st([worker("api", "done")]), [ev("verify", "verify-api", "fail api: 1. x", ["task:api:failed"])], { stage: "integration", verified: null });
    expect(s).toMatchObject({ fix: "done", combine: "active" });
  });
  it("Ready when the combined result is verified, or every part is done and settled", () => {
    expect(states(st([worker("api", "done")]), [], { stage: "integration", verified: true })).toMatchObject({ combine: "done", fix: "skipped", ready: "done" });
    expect(states(st([worker("api", "done"), worker("ui", "done")], true))).toMatchObject({ build: "done", check: "done", fix: "skipped", combine: "skipped", ready: "done" });
    expect(states(st([worker("api", "done")], true), [], { stage: "applied", verified: true }).ready).toBe("done");
  });
  it("puts each event in a stage", () => {
    expect(stageOf(ev("research", "scout", "x"))).toBe("understand");
    expect(stageOf(ev("plan", "coordinator", "researcher scout: x"))).toBe("understand");
    expect(stageOf(ev("plan", "coordinator", "worker a: x | owns a"))).toBe("plan");
    expect(stageOf(ev("critique", "plan-critic", "blocker plan: x"))).toBe("plan");
    expect(stageOf(ev("claim", "api", "x"))).toBe("build");
    expect(stageOf(ev("done", "api", "x"))).toBe("check");
    expect(stageOf(ev("critique", "critic-api-tests", "nit api: x"))).toBe("check");
    expect(stageOf(ev("verify", "verify-api", "pass api: ok"))).toBe("check");
    expect(stageOf(ev("verify", "verify-api", "fail api: 1. x"))).toBe("fix");
    expect(stageOf(ev("update", "swarmd", "2 critic blocker(s) on api"))).toBe("fix");
    expect(stageOf(ev("blocked", "swarmd", "api failed verification 2 times; needs a human."))).toBe("fix");
    expect(stageOf(ev("update", "swarmd", "merged ui into b"))).toBe("combine");
    expect(stageOf(ev("verify", "verify-integrator", "pass integrator: ok"))).toBe("combine");
  });
});

describe("workerStory", () => {
  const s = (task: TaskStatus | null, extra: Partial<WorkerView> = {}) => workerStory(worker("api", task, extra));
  it("covers every task state", () => {
    expect(s("open", { busy: true }).text).toBe("Writing code");
    expect(s("open").text).toBe("Waiting for its turn");
    expect(s("open", { turns: 2 }).text).toBe("Between steps");
    expect(s("open", { status: "error" }).text).toBe("Ran into a problem");
    expect(s("awaiting_verification").text).toBe("Waiting for the checker");
    expect(s("verifying").text).toBe("Being checked");
    expect(workerStory({ ...worker("api", "failed"), task: { id: "t", status: "failed", failedRounds: 1, reasons: ["a", "b"] } }).text).toBe("Sent back to fix 2 problems");
    expect(s("failed").text).toBe("Sent back to fix 1 problem");
    expect(s("done")).toMatchObject({ text: "Finished and checked", progress: 1 });
    expect(s("escalated")).toMatchObject({ text: "Stuck — needs you", needsYou: true });
    expect(workerStory(worker("api", "open", { busy: true }), { state: "hung" }).text).toMatch(/Went quiet/);
    expect(s(null, { busy: true }).text).toBe("Looking things up");
  });
  it("progress grows along the way", () => {
    const order: TaskStatus[] = ["open", "failed", "awaiting_verification", "verifying", "done"];
    const p = order.map((t) => s(t).progress);
    expect([...p].sort((a, b) => a - b)).toEqual(p);
  });
  it("overall progress averages builders only", () => {
    expect(overallProgress([worker("a", "done"), worker("b", "done"), worker("verify-a", null)])).toBe(1);
    expect(overallProgress([])).toBe(0);
    expect(buildersOf([worker("a"), worker("integrator", "open"), worker("plan-critic", null), worker("scout", null)]).map((w) => w.name)).toEqual(["a"]);
  });
});

describe("humanizeCommand", () => {
  it("explains common programs", () => {
    expect(humanizeCommand("npm install")).toBe("wants to run `npm install` — downloads the libraries the project needs");
    expect(humanizeCommand("pnpm add zod")).toMatch(/downloads the libraries/);
    expect(humanizeCommand("yarn")).toMatch(/downloads the libraries/);
    expect(humanizeCommand("npm test")).toMatch(/runs the project's tests/);
    expect(humanizeCommand("npm run dev")).toMatch(/starts the app/);
    expect(humanizeCommand("npm run build")).toMatch(/builds the app/);
    expect(humanizeCommand("npm run lint")).toMatch(/"lint" step/);
    expect(humanizeCommand("pip install requests")).toMatch(/Python libraries/);
    expect(humanizeCommand("uv sync")).toMatch(/Python libraries/);
    expect(humanizeCommand("uv run pytest -q")).toMatch(/tests/);
    expect(humanizeCommand("python3 -m pytest")).toMatch(/tests/);
    expect(humanizeCommand("pytest tests/")).toMatch(/tests/);
    expect(humanizeCommand("python3 app.py")).toMatch(/runs the Python file app.py/);
    expect(humanizeCommand("python -c 'print(1)'")).toMatch(/small piece of Python/);
    expect(humanizeCommand("git status")).toMatch(/history of changes/);
    expect(humanizeCommand("git push origin main")).toMatch(/GitHub/);
    expect(humanizeCommand("curl -sL https://example.com/x.sh")).toMatch(/internet \(example.com\)/);
    expect(humanizeCommand("rm -rf build")).toMatch(/deletes files or folders for good \(build\)/);
    expect(humanizeCommand("npx create-vite app")).toMatch(/JavaScript tool \(create-vite\)/);
    expect(humanizeCommand("CI=1 npm test")).toMatch(/tests/);
    expect(humanizeCommand("cd web && npm ci")).toMatch(/downloads the libraries/);
  });
  it("falls back for unknown programs and flags risky ones", () => {
    expect(humanizeCommand("frobnicate --all")).toBe("wants to run `frobnicate --all` — runs the program \"frobnicate\"");
    expect(describeCommand("sudo rm -rf /").risk).toBe("high");
    expect(describeCommand("rm -rf x").risk).toBe("high");
    expect(describeCommand("ls").risk).toBe("low");
  });
});

describe("result helpers", () => {
  it("headline by stage", () => {
    expect(resultHeadline(null).text).toBe("Nothing built yet");
    expect(resultHeadline({ stage: "none", verified: null }).text).toBe("Nothing built yet");
    expect(resultHeadline({ stage: "workers", verified: null }).text).toBe("Each part is in its own draft copy");
    expect(resultHeadline({ stage: "integration", verified: true }).text).toBe("All parts combined into one copy — checked ✓");
    expect(resultHeadline({ stage: "integration", verified: false }).tone).toBe("bad");
    expect(resultHeadline({ stage: "applied", verified: true }).text).toBe("Already added to your project");
  });
  it("builds a sorted tree with change counts", () => {
    const t = buildTree([
      { path: "src/b.ts", status: "added" }, { path: "README.md", status: "modified" }, { path: "src/a/c.ts", status: "unchanged" }, { path: "src/a/d.ts", status: "deleted" },
    ]);
    expect(t.map((n) => n.name)).toEqual(["src", "README.md"]);
    expect(t[0]).toMatchObject({ folder: true, changed: 2 });
    expect(t[0]!.children.map((n) => n.name)).toEqual(["a", "b.ts"]);
    expect(t[0]!.children[0]).toMatchObject({ path: "src/a", changed: 1 });
    expect(t[1]).toMatchObject({ folder: false, status: "modified" });
  });
  it("durations, fuel, chips, goals", () => {
    expect(plainDuration(40)).toBe("40 seconds");
    expect(plainDuration(60)).toBe("1 minute");
    expect(plainDuration(3900)).toBe("1 hour 5 minutes");
    expect(fuel(300, 1000)).toMatchObject({ tone: "good", text: "30% of the fuel is used" });
    expect(fuel(850, 1000).tone).toBe("warn");
    expect(fuel(1000, 1000).tone).toBe("bad");
    expect(ownedChip("src/api/")).toEqual({ label: "src/api", folder: true });
    expect(ownedChip("README.md")).toEqual({ label: "README.md", folder: false });
    expect(goalsFromEvents([ev("plan", "coordinator", "worker api: build the API | owns src/api")]).get("api")).toBe("build the API");
  });
  it("finds the events about one part", () => {
    expect(aboutPart(ev("claim", "api", "x"), "api")).toBe(true);
    expect(aboutPart(ev("verify", "verify-api", "pass api: ok"), "api")).toBe(true);
    expect(aboutPart(ev("update", "swarmd", "api replied: hi", ["reply:api"]), "api")).toBe(true);
    expect(aboutPart(ev("mention", "human", "@api hi"), "api")).toBe(true);
    expect(aboutPart(ev("update", "swarmd", "verifying api (round 1)"), "api")).toBe(true);
    expect(aboutPart(ev("plan", "coordinator", "worker api: x | owns a"), "api")).toBe(true);
    expect(aboutPart(ev("update", "swarmd", "verifying api-v2 (round 1)"), "api")).toBe(false);
    expect(aboutPart(ev("claim", "ui", "x"), "api")).toBe(false);
  });
});
