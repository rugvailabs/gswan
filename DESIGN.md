# gswan — Full /swarm for Claude Code (design v1)

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
