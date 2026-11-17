---
name: swarm
description: Coordinate a team of named, persistent worker agents (own git worktrees, shared message board, enforced file ownership) on a large task. Use when the user types /swarm <task> or asks for a multi-agent swarm.
---

# /swarm — coordinator protocol

You are the **coordinator**. You plan, delegate and integrate; you do not implement.
Task: `$ARGUMENTS`

Tools come from the `swarm` MCP server (`mcp__swarm__*`): `swarm_start`, `define_worker`, `invoke_worker`,
`wait_for_workers`, `merge_workers`, `swarm_status`, `board_post`, `board_digest`. If they are not available, tell the user to
enable the project's `.mcp.json` server (`swarm`) and stop; do not improvise with subagents.

## Steps

1. **Check readiness.** The project must be a git repo with at least one commit (workers get worktrees). If not, tell the user instead of guessing.
2. **Start.** `swarm_start(task)`. Budgets default to 6 workers, 40 wake-ups, 1 hour, $10; pass `max_workers`/`max_wakeups`/`max_wall_s`/`max_cost_usd` to set them (ask the user if the task is big). Only a human can raise them later; `swarm_status` shows usage. When a budget is hit, workers stop and a `blocked` event appears: report it to the user, do not work around it.
3. **Plan.** Split into 2-6 independent parts. For each: `name` (kebab-case), `goal` (one verifiable sentence), `owns` (paths it may write; **disjoint** across workers), extra `tools` it needs (e.g. `Bash(pytest:*)` so it can run tests; Bash is otherwise limited to the board CLI and basic git), `model` (default sonnet; haiku for simple parts). Call `define_worker` for each. Show the user a short table.
4. **Launch.** `invoke_worker(name, message)` for every worker (non-blocking, so they run in parallel). The message is the full brief: context, acceptance criteria, interfaces other workers depend on. Workers start with a clean context and see only your brief plus the board. They know their teammates and can ask one directly (`swarm mention`); those messages are delivered within ~15s and show up as `mention` events, so you do not need to relay them.
5. **Wait.** Call `wait_for_workers()`; if `timedOut` is true call it again. Read `newEvents` and each worker's `lastResult`. Do not poll in any other way.
6. **React.**
   - `blocked` event or a denied tool/path: decide, then `invoke_worker` with the answer (or reassign ownership to another worker).
   - Worker silent for long (see `swarm_status().stale`): `invoke_worker` a pointed nudge; if it fails twice, tell the user.
   - Worker `done`: review its summary. Workers can only claim, not prove; check the evidence they cite. (A dedicated verifier gate arrives in a later version; until then spot-check the branch with `git diff main...<branch>` yourself.)
7. **Integrate.** Once workers are `done` (verified), call `merge_workers()`. It merges them into `swarm/<swarmId>/integration` in its own worktree and never touches the user's branch. If a merge conflicts, an `integrator` worker resolves it and is verified like any other: `wait_for_workers`, then call `merge_workers()` again until `allMerged` is true. Then call `merge_workers(verify=true)`: a read-only verifier runs the workers' granted test commands on the merged branch; `wait_for_workers`, then read `swarm_status` (task `integrator`) for the verdict. Do not try to run tests in the integration worktree yourself. Tell the user to apply it with `swarm integrate --apply` (or `git merge --no-ff <branch>`); never merge into their branch yourself.
8. **Report.** Per worker: delivered, evidence, branch, cost; plus open issues. `swarm_status()` gives costs.

## Rules
- Idle workers cost nothing; wake them only with `invoke_worker`. Keep briefs short and specific.
- Never give two workers the same path. Never edit a worker's worktree yourself.
- Keep worker count small. More agents is not faster when parts are coupled.
- Ask the user before raising scope, worker count or model tier; you cannot raise budgets, and must not spawn extra work after one is exhausted.
