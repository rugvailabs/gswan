# gswan — `/todo` for Claude Code (Antigravity's `/swarm`; design v1, extended 2026-11-19)

Naming: user-facing command/plugin is **todo** (`/todo:todo`, CLI alias `todo`). Internals keep `swarm`/`swarmd`/`.swarm/` to avoid breaking existing runs and branch names (`swarm/<id>/*`).

Status: design, 2026-11-17. Source of truth is the user's description of Google Antigravity `/swarm`
(REA found no swarm code in Antigravity 1.22.2; only the subagent layer). Anything marked **[ours]** is our own design.

## 1. Requirements (from the description)
1. `/swarm <task>` makes the current chat a **coordinator** that splits work and assigns **named workers**.
2. Each worker: clean context, clear responsibility, defined goal.
3. **Idle workers cost zero tokens** until `@`-mentioned.
4. **Verifier** reviews changes, runs tests, pushes back on errors.
5. **Monitor** intervenes when work stalls.
6. **Live shared message board**: plans, builds, updates visible to everyone (and the human).
7. Works from CLI and desktop app.

## 2. Hard constraints
- Claude Code is closed source: integrate only via plugin, MCP server, hooks, skills, subagent files, and `claude -p` (headless).
- Subagents spawned with the `Agent` tool are scoped to a turn; they cannot be the long-lived, mention-woken workers. Workers must be **their own `claude` sessions** owned by our service.

## 3. Architecture

```
 Claude Code session (coordinator)
   │  /swarm skill + MCP tools  ◄────────── hooks (verifier gate, stall nudges)
   ▼
 swarmd  (local daemon, FastAPI + SQLite, 127.0.0.1)
   ├─ Board         append-only event log + SSE stream
   ├─ Registry      workers: name, goal, owns, session_id, status, worktree
   ├─ Runner        spawns/resumes `claude -p --resume <session_id>` per worker
   ├─ Verifier gate runs verifier worker, records pass/fail per task
   ├─ Monitor loop  heartbeat + progress checks, posts nudges, can restart
   └─ MCP server    (stdio shim → swarmd HTTP) exposing tools to every agent
        ▲
 Web UI (React+TS+Tailwind+Zustand): live board, worker cards, @mention box
```

### 3.1 Workers (requirement 2, 3)
- A worker = a persistent `claude` session identified by `session_id` + git worktree `.swarm/wt/<name>` (isolation; no file clashes).
- Runner starts it with `claude -p "<mission + board digest>" --session-id <uuid> --output-format stream-json --allowedTools <scoped> --permission-mode <policy>` in its worktree.
- When the turn ends the process exits: state **idle, zero tokens**. Context lives in the session transcript.
- `@name message` (from coordinator, human, or another agent) → Runner does `claude -p "<message>" --resume <session_id>`. Only then are tokens spent.
- Clean context: first prompt contains only mission, owned paths, plan excerpt. No coordinator transcript.
- **One turn at a time per worker (spike-verified):** concurrent `--resume` of the same session silently loses a turn. Runner keeps a per-worker FIFO queue; `@mention`s that arrive while busy are queued (and merged if consecutive). Different workers run in parallel.
- Resume works from any cwd; `--settings` needs an absolute path; hooks see env vars set on the spawned process (`SWARM_WORKER`, `SWARM_OWNS`).

### 3.2 Board (requirement 6)
- SQLite table `events(id, ts, agent, kind, task_id, body, refs)`; kinds: `plan claim update blocked done verify nudge mention human`.
- SSE endpoint `/events`; UI and CLI `tail` subscribe. Markdown export `.swarm/board.md` kept for git and for agents without MCP.
- Agents see the board through MCP tools, not by reading all history: `board_digest(since)` returns a compact summary (cost control).

### 3.3 MCP tools (all agents; role-filtered)
| Tool | Who | Effect |
|---|---|---|
| `swarm_start(task)` | coordinator | create swarm, board |
| `define_worker(name, goal, owns[], tools[])` | coordinator | register worker (mirrors Antigravity `define_subagent`) |
| `invoke_worker(name, message)` | coordinator, workers | Runner wake/resume (`@mention`) |
| `board_post(kind, body, task_id?)` | all | append event |
| `board_digest(since?)` | all | compact board view |
| `request_verification(task_id)` | worker | queue verifier; worker's `done` is **provisional** |
| `submit_verdict(task_id, pass, reasons[])` | verifier | pass/fail |
| `swarm_status()` | coordinator, human | workers, tasks, stalls |

### 3.4 Verifier (requirement 4) — enforced, not requested
- `done` from a worker only sets task `awaiting_verification`.
- Runner wakes the `verifier` worker (read-only tools + test runner, runs in the worker's worktree, never edits source).
- `fail` → reasons are auto-delivered to the worker via `invoke_worker`; max 2 rounds then `blocked` + escalate to human. `pass` → task `done`.
- **Hook gate [ours]:** `Stop` hook on the coordinator session blocks ending the swarm while any task is not `done` (exit code 2 + reason).

### 3.5 Monitor (requirement 5)
- Runs inside swarmd (no LLM needed for detection): per worker track last event time, last tool activity from stream-json, and repeated-identical-update count.
- Stalled = running > N min with no event/tool activity; looping = same update ≥3 times. Actions in order: post `nudge`, `invoke_worker` with a pointed prompt, then kill/restart session with digest, then escalate to coordinator/human.
- Optional LLM check **[ours]**: cheap model summarizes "is this progress?" only on suspected stalls.

### 3.6 Coordinator + UX (requirement 1, 7)
- Plugin ships: `/swarm` skill, `swarm-*` agent files (kept as fallback for no-daemon mode), MCP server config, hooks, and `swarmd` launcher (`swarm up` auto-started by `SessionStart` hook).
- `/swarm <task>`: coordinator calls `swarm_start`, plans, `define_worker` ×N, then `invoke_worker` for each (parallel), then waits on completion events (no polling), integrates, reports.
- Human can open the web UI, watch the board, and `@worker` or `@verifier` directly.

## 4. Safety / policy
- Workers' `--allowedTools` scoped per worker; path ownership enforced by a `PreToolUse` hook that denies Write/Edit outside `owns[]` (per-worker env var `SWARM_WORKER`).
- No secrets in board events (redaction filter on `board_post`).
- Per-swarm budget: max workers, max wake-ups, max wall time; coordinator must ask before raising.
- Daemon binds 127.0.0.1 only, token in `.swarm/token`.

## 5. Stack (user defaults)
Python 3.12 + FastAPI + Pydantic + SQLite; MCP via official Python SDK; UI React+TS+Tailwind+Zustand; tests pytest, vitest, Playwright; entity IDs prefixed (`swm_`, `wrk_`, `tsk_`, `evt_`); API kebab-case, JSON camelCase, paginated lists.

## 6. Build plan (vertical slices)
1. **S1 Core:** SQLite board + FastAPI + SSE + `board.sh` replaced by CLI `swarm`. Tests.
2. **S2 Runner:** worktree + `claude -p --session-id/--resume`, wake/idle lifecycle, stream-json parsing. Spike first: confirm resume keeps context and idle = no process.
3. **S3 MCP + coordinator:** tools above, `/swarm` skill rewritten on them.
4. **S4 Verifier gate + hooks:** verdict flow, Stop hook, PreToolUse ownership hook.
5. **S5 Monitor:** detection + escalation ladder.
6. **S6 UI:** live board, worker cards, @mention.
7. **S7 Plugin packaging + docs.**

## 7. Risks / open questions
- Headless `claude -p` auth and permission prompts for workers (need explicit `--permission-mode`/allowed tools); verify in S2 spike.
- `gswan` is not a git repo; worktrees require `git init` (or per-worker directory copies).
- Cost: N workers + verifier multiplies tokens; mitigate with digests, small worker counts, cheaper model for monitor/verifier.
- If a newer Antigravity build exposes real `/swarm` internals, diff against this design (esp. board schema, @mention semantics).

## 8. Extension: research, critics, transparency, evals (S8-S11)

Goal: more capable (agents that find facts and attack weak work), more transparent (every decision explainable), and measurable.
New roles reuse the worker registry, runner, board and limits. Roles: `worker`, `verifier`, `researcher`, `critic`.

### S8 Research agents (done: core)
- `researcher` role: read-only (Read/Glob/Grep/WebSearch/WebFetch), runs in the project root, no task, no worktree, no verifier gate, not counted against `max_workers`; own cap of 4 per swarm; still counted in wake-ups and cost.
- Output is board kind `research`: `claim | https://url | "verbatim quote"`, or `UNVERIFIED: ...`. `swarm post` rejects a research post without a URL (or UNVERIFIED) and rejects the kind from non-researchers.
- Safety: web/repo content is data, never instructions (charter says so). Researchers cannot write, cannot mention workers, Bash is read-only (shared `READ_ONLY_ROLES` in `bashpolicy.py`). The coordinator decides what reaches a writing worker, and passes it in the brief.
- Tools: `define_researcher(name, question)`, then `invoke_worker`. Skill step 3 (optional).
- Open: **citation spot-check** (a verifier re-fetches N quotes and fails the finding if the quote is absent); research-aware `board_digest`.

### S9 Critic agents (done: plan critic, diff critics, gating; open: dispute flow)
Verifier asks "does it meet the task" after the work. Critics attack the work and the plan.
- **Plan critic**: read-only, runs after the coordinator's plan and before any worker is invoked. Checks missing requirements, overlapping ownership, risky assumptions, untestable goals. Output: findings with severity.
- **Diff critic**: one per worker (personas: correctness, security, tests/maintainability; cheap model for nits). Receives the task + `git diff <base>`, NOT the worker's reasoning. Runs before the verifier so the verifier sees fixed work.
- Severity `blocker | major | nit`. Only blockers gate (worker gets them like a failed verdict); majors/nits are posted (`critique` kind) and listed in the report. Max 2 critic rounds per task, then escalate (same ladder as the verifier).
- Anti-ping-pong: a finding must name file:line and a concrete failure; the worker may answer `disputed: <reason>`, which goes to the verifier to arbitrate; per-role budget so critics cannot eat the worker budget.
- Different from workers by model/prompt/inputs, otherwise they echo the worker.

- Built: `critics.py` (findings table, rounds, severity), role `critic` (Read/Glob/Grep + read-only git), `swarm critique <target> blocker|major|nit|ok "reason" --at file:line` (critic-only; blocker/major need file:line), gate runs persona critics in parallel before the verifier when `critics=true` at `swarm_start` or `SWARM_CRITIC_MODEL` is set (default off), blockers -> worker without spending a verifier round, after 2 rounds unresolved blockers go to the verifier brief. MCP `critique_plan`. Not built: `disputed:` replies, per-role budgets.

### S10 Transparency (done except replay timeline)
- Provenance on events: agent, session, the tool calls behind a claim (`refs`), cost of the turn.
- `decision` board kind: coordinator records why it split/merged/escalated/skipped a critic finding.
- Plan-vs-actual per worker: owned paths vs files changed, goal vs verdict; scope drift flagged.
- Monitor explainability: every nudge/kill records the signal and thresholds that triggered it.
- UI: per-worker diff view with critic/verifier verdicts, researcher findings panel with source links, denied.log, cost by role, replay timeline; `swarm report` exports a markdown run report.

- Built: `swarmd/report.py` + `swarm report [--out] [--json]` + `GET /report` + UI "Run report" panel: budget, cost by role, plan-vs-actual per worker (git diff base..branch vs `owns`, scope drift flagged), decisions, research, critic findings, verdicts, monitor actions (nudges now include the rule/thresholds that fired), denied-call tail. `decision` board kind (coordinator logs why). Worker reply events carry provenance refs `cost:`, `turn:`, `session:`. Not built: replay timeline.
- Diff viewer: `swarmd/diffs.py` + `GET /workers/{name}/diff` (committed changes `base..branch` per file: status, +/- counts, patch capped at 60k chars, max 200 files, out-of-scope flag, uncommitted paths listed, credential-shaped strings hidden via `redact_secrets`, plus the worker's critic findings and verifier verdicts). UI `DiffPanel` ("diff" button on each worker card): line-numbered patches, findings shown on the file they cite with the cited line marked, verdicts, scope drift.
- Enforced decisions: `swarmd/decisions.py` derives what the coordinator owes from swarm state (`[split]` once workers exist, `[escalation <worker>]` per escalated task, `[critic plan|<worker>]` while last-round critic blockers are open on the plan or on an accepted worker). Settled only by a tagged `decision` event from the coordinator/human with a reason (>= 20 chars) newer than the thing it explains. MCP `record_decision`; `merge_workers` and the Stop hook refuse while any is missing; `swarm_status.missingDecisions` and the report list them. Humans (`swarm integrate`) are not gated.

### S11 Eval harness (done: harness + 2 tasks, validated offline; no real run yet)
- 5-10 fixed tasks in `evals/` (each: repo fixture, prompt, deterministic check). `swarm eval` runs single-agent vs swarm vs swarm+research+critics and records pass rate, cost, wall time, escalations.
- Run on every change to roles/prompts/limits. No new role ships without showing it moves a number or the report says so.

- Built: `swarmd/evals.py`, `swarm eval [--task] [--mode] [--repeat] [--max-cost] [--yes]` (dry run without --yes, prints worst-case spend). `evals/tasks/<id>/{task.json,repo/,check/,reference/}`: agents see only `repo/`; `check/` (hidden unittest) is copied in after the run; `reference/` proves the check passes a correct solution and fails the starting repo (tested). Modes: single, swarm, swarm+research, swarm+critics, swarm+all. For swarm modes the harness (not the coordinator) applies the integration branch, and cost = coordinator + all agents' `cost_usd`. Tasks: textkit (3 independent modules), inventory-bugs (fix deviations from a spec). Results go to `evals/results/` (gitignored).
- Caveats: 2 tasks x 1 run is an anecdote; use `--repeat 3+` and add tasks before drawing conclusions. The headless `claude -p /todo:todo` invocation and the `mcp__plugin_todo_swarm` allow-list name are unverified against a real run.

### Risks
Cost multiplication (per-role budgets), critic loops (hard caps, blocker-only gate), prompt injection via research (read-only + data-only + coordinator filter), board noise (collapse by kind, digest), same-model blind spots (diversify model/prompt/input).
