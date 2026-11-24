# todo — multi-agent `/todo` for Claude Code (Antigravity's `/swarm`)

A coordinator splits a big task across **named, persistent workers**. Each worker is its own Claude session in its own git worktree, can only write inside the paths it owns, and costs nothing while idle. A **read-only verifier** checks every `done`, a **monitor** catches hung or looping workers, and everything is visible on a **live message board** (CLI, MCP tools and a web UI).

Modelled on the behaviour described for Google Antigravity's `/swarm` (coordinator, named workers, verifier, monitor, shared board). Antigravity 1.22.2 contains no `/swarm` code, so the verifier, monitor and board here are our own design from that description. See `DESIGN.md`.

## Run report
`swarm report` (also the **Run report** panel in the UI, or `GET /report`) shows, after or during a run: budget and tokens by role, each worker's planned paths vs the files its branch actually changed (scope drift is flagged), the coordinator's recorded decisions (and any required one it never explained), research findings with sources, critic findings, verifier verdicts, monitor actions with the rule that fired, and recent denied tool calls.

Each worker card in the dashboard also has a **diff** button: the files its branch changed since its base commit, with line-numbered patches, files outside its owned paths flagged, and the critic findings and verifier verdicts shown next to the code they refer to. Only committed changes are shown; uncommitted paths are listed by name. The board also has a **Graph** tab (who messaged whom, arrows weighted by count, a slider to see it at any point, and a table of the same data) and a **Replay** tab: step or play through the run event by event and see each agent's task status, last message and cost as they stood at that moment.

## Evals
`swarm eval` compares one agent against the swarm modes (`single`, `swarm`, `swarm+research`, `swarm+critics`, `swarm+all`) on fixed tasks in `evals/tasks/`, scored by hidden tests the agents never see. It spends real money, so it is a dry run (it prints the run count and worst-case spend) until you add `--yes`. Example: `swarm eval --task textkit --mode single --mode swarm --repeat 3 --max-cost 2 --yes`; results land in `evals/results/`.

## Requirements
- Claude Code (tested with 2.1.326) signed in; headless workers use the same auth (`claude auth login` / `claude setup-token`)
- [`uv`](https://docs.astral.sh/uv/) (installs the Python deps on first use) and `git`
- Your project must be a git repo **with at least one commit** (workers get worktrees)

## Install
As a plugin (from a local clone or any git URL):
```
/plugin marketplace add /path/to/gswan
/plugin install todo@todo
```
Or try it without installing: `claude --plugin-dir /path/to/gswan`.

The plugin adds: the `/todo:todo` skill, the `swarm` MCP server, and two hooks (env warm-up on session start; a Stop hook that keeps the coordinator from finishing while tasks are unverified). The first session after install runs `uv sync` in the background; if the server is not up yet, run `/mcp` once and reconnect.

## Use
```
/todo:todo Build a todo CLI with tests: storage, commands, and docs as separate parts
```
The coordinator will: start a swarm, define workers with disjoint owned paths, wake them in parallel, wait, react to `blocked` events, then call `merge_workers`, which merges every verified worker branch into `swarm/<swarmId>/integration` in its own worktree (your checked-out branch is never touched). A conflict is handed to an `integrator` worker, which is verified like any other. When it reports `allMerged`, apply the result yourself:
```
swarm integrate --apply      # merges the integration branch into your current branch (refuses on a dirty tree)
```
Give the coordinator `Bash(python3:*)` (or your test command) if you want it to run the tests in the integration worktree.

Workers can also ask each other for things (`swarm mention <worker> "..."`, delivered by the monitor within ~15 s, max 5 messages per pair). Workers can belong to a `group`: `@<group>` reaches every member and `@all` every worker, from the coordinator, the dashboard, the CLI or another worker (one board event; the per-pair cap still counts each recipient).

Switch an idle worker to another model without losing its conversation: the model menu on its card, `swarm worker model <name> haiku --why "..."`, or the coordinator's `set_worker_model` (which must say why; the reason is recorded as a decision). The first turn on the new model re-reads the conversation without the prompt cache, so it costs more than usual.

Budgets protect you: each swarm defaults to 6 workers, 40 wake-ups, 1 hour and 3M tokens (input + output; tokens re-read from the prompt cache are shown but not counted). The coordinator can pick lower or higher values at `swarm_start` (tell it in your prompt); only you can raise them mid-run: `swarm limits --set max_tokens=6000000`. When a budget is hit, workers stop, a `blocked` event is posted and queued messages wait.

Watch and steer from the browser:
```
uv run --project /path/to/gswan swarm serve      # prints http://127.0.0.1:7777/?token=...
```
Cards per worker (status, task, monitor warnings, activity, tokens), the live board with filters, and a box to `@mention` a worker or post a note.

**Needs you.** When a worker's command is denied by the policy, it becomes a request on the dashboard: *Allow this command* (that exact command, for that worker), *Allow all `<program>` commands* (adds the grant) or *Deny* (with a note). The worker is told not to wait, and is woken with your answer. From the terminal: `swarm approvals`, `swarm approve <id> [--program]`, `swarm deny <id> --note "..."`. Agents cannot answer requests or change budgets.

**Pause, not fail.** When a budget runs out, work pauses: queued messages stay queued and tasks waiting for verification are held, not escalated. *Raise by 50% and resume* on the dashboard (or `swarm limits --set ...`) picks everything up where it stopped.

## How it works
| Piece | What it does |
|---|---|
| Workers | `claude -p --session-id/--resume` per worker, own worktree + branch. Idle = no process = no tokens. `@mention` = resume. One turn at a time per worker (extra mentions are queued and merged). |
| Ownership | `PreToolUse` hook denies Write/Edit outside the worker's `owns`. Bash goes through a command-policy hook: the board CLI, git (status/diff/log/add/commit, merge of this swarm's branches), mkdir/touch inside the worktree, read-only tools, and only the commands you grant at `define_worker`. Env prefixes (`PYTHONPATH=src`), `cd <subdir> &&`, `;` and pipes between allowed commands, `sed -n` reads, `find` without actions, `git -C` reads inside this swarm's worktrees and plain shell variables (`P=value; $P ...`, never overriding an existing variable) work; redirections, `$(...)`, backticks, background jobs and `cd` outside the worktree are denied. |
| Verifier | A worker's `done` is provisional. A read-only `verify-<worker>` session inspects the diff, runs the worker's required `checks` (a pass is refused until each has run) and gives a verdict. Fail sends numbered reasons back to the same worker; two failed rounds (or no verdict twice) escalate to you. |
| Critics | Optional (`critics=true`). Read-only diff review before the verifier: one combined critic for a small diff (< 200 changed lines), correctness/security/tests critics for a big one, none for a docs-only diff. Blockers send work back (max 2 rounds); majors go back once to fix or explain, then the verifier checks. |
| Monitor | Detects *hung* (no output), *looping* (identical tool calls / posts) and *forgot to report* without an LLM. Ladder: nudge, kill and resume with a restart prompt, escalate. Optional `--llm-judge` / `SWARM_MONITOR_LLM=1`: a cheap Haiku call (no tools, $0.05 cap) is asked whether a quiet worker is legitimately busy (long build/tests) before it is killed, up to twice per episode. It also dispatches peer mentions. |
| Integration | `merge_workers` / `swarm integrate`: clean merges in order, conflicts to a verified `integrator` worker, result on `swarm/<id>/integration`, applied by you. |
| Budgets | Per-swarm caps on workers, wake-ups, wall time and tokens, enforced before every turn. Usage is shown in tokens, not as a price. |
| Tokens | Per worker: input (fresh input + cache writes), output, and cached (cache reads) as reported by `claude -p`. |
| Board | SQLite event log (`.swarm/swarm.db`), mirrored to `.swarm/board.md`; secrets are redacted before storage. |
| Stop hook | Blocks the coordinator (max 3 times) while any task is not `done`/`escalated`. |

MCP tools (coordinator): `swarm_start` (`critics=true` adds diff critics), `define_worker`, `define_researcher` (read-only web/repo research with cited `research` events), `critique_plan` (read-only plan critic),  `invoke_worker` (non-blocking), `wait_for_workers` (≤110 s, call again if `timedOut`), `merge_workers`, `record_decision`, `swarm_status`, `board_post`, `board_digest`.

CLI: `swarm init|post|show|tail|digest|stale|export|tasks|limits|mention|integrate|monitor|serve`, `swarm worker define|list|invoke`, `swarm verdict` (verifier role only; `--force` for humans).

## Configuration (environment)
| Variable | Default | Meaning |
|---|---|---|
| `SWARM_HOME` | nearest `.swarm/`, else git root, else cwd | project root |
| `SWARM_VERIFIER_MODEL` | `sonnet` | model for verifiers |
| `SWARM_CRITIC_MODEL` | off | model for diff critics on every swarm (or pass `critics=true` at swarm_start) |
| `SWARM_MONITOR` | `1` | `0` disables the monitor thread |
| `SWARM_MONITOR_INTERVAL` | `15` | seconds between monitor checks |
| `SWARM_MONITOR_LLM` | off | `1` enables the Haiku stall judge |
| `SWARM_CLAUDE_BIN` | `claude` | path to the claude binary |

`.swarm/` ignores itself, so a swarm never dirties your repo status.

## Safety model and limits
- Workers run with `--permission-mode dontAsk`; file tools are allowlisted, every Bash call is judged by `swarmd/bashpolicy.py` (strict parser, tested against evasion attempts). Each turn is capped at $2 and each swarm by its budgets. Check `swarm worker list` / `swarm limits`.
- Writing inside any `.git` directory is denied (a crafted nested `.git/config` could make `git status` run a command).
- The ownership hook covers file-write tools. Bash redirections are refused, but a granted program such as `python3` can still write anywhere (python3 -c is arbitrary code), and read commands (cat, grep) can read any file you can. Grant narrowly: `Bash(python3 -m pytest:*)` instead of `Bash(python3:*)`. The policy removes friction; it is not a sandbox.
- The server binds to 127.0.0.1 and requires the token in `.swarm/token`.
- Monitor thresholds (300 s of silence = hung) are defaults; a long legitimate command looks like a hang. Tune with `swarm monitor --stall-s`.
- Conflict resolution is done by a model (the integrator) and verified by a read-only verifier, but still review the integration branch before `--apply`.
- Platforms: developed and tested on macOS. Linux should work (same POSIX process-group logic) but is untested. **Windows is not supported** (POSIX process groups, `sh` hook commands).
- Plugin path installs (`/plugin marketplace add /local/path`) run the plugin from that folder in place; GitHub installs copy only committed files (~1 MB).

## Develop
```
uv run pytest                      # backend tests
cd ui && npm install
npm test                           # vitest tests
npm run build                      # writes ui/dist (committed so the plugin ships it)
npm run e2e                        # 6 Playwright tests, system Chrome, real server + fake claude
claude plugin validate .
```
Layout: `swarmd/` (board, workers, runner, gate, monitor, api, mcp_server, cli), `skills/swarm/`, `hooks/`, `.claude-plugin/`, `ui/`, `tests/`.

License: MIT (see `LICENSE`).
