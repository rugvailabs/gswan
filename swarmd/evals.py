"""Eval harness: does the swarm (and each extra role) beat one agent on the same task?

`evals/tasks/<id>/` holds `task.json` (prompt, tools, timeout), `repo/` (starting files), `check/` (HIDDEN unittest files the agents
never see; copied in only after the run) and `reference/` (a known-good solution, used by tests to validate the check itself).
Modes: single | swarm | swarm+research | swarm+critics | swarm+all. Metrics per run: pass, cost, wall seconds, escalations.
Real runs spend money: the CLI refuses to run without --yes and prints the worst-case spend first."""
from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import tokens
from .board import Board
from .gate import Gate
from .integrate import Integrator
from .runner import Exec, Runner, subprocess_executor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODES = ("single", "swarm", "swarm+research", "swarm+critics", "swarm+all")
Executor = Callable[[list[str], Path, dict[str, str], float], Exec]

# Measured on 2026-11-24 real runs (sonnet coordinator/verifiers/critics, mixed workers): $5.45 for 962k tokens (input + output).
USD_PER_MILLION_TOKENS = 6.0
DEFAULT_MAX_TOKENS = 1_500_000  # per swarm run, enforced through SWARM_TOKEN_CEILING

RESEARCH_HINT = "Before planning, define 1-2 researchers (define_researcher) for facts you genuinely lack, wait for them, and use their cited findings."
EVAL_RULES = ("[AUTOMATED EVAL: no human will answer questions. Do not ask any. When merge_workers reports allMerged (and merge_workers(verify=true) "
              "has finished), stop and report. Do NOT apply or merge the integration branch yourself; the harness does that.]")


@dataclass
class EvalTask:
    id: str
    prompt: str
    tools: list[str]
    timeout_s: int
    dir: Path


@dataclass
class RunResult:
    task: str
    mode: str
    passed: bool
    cost_usd: float
    wall_s: float
    tokens: int = 0  # input + output (cache reads not counted), coordinator/agent plus every swarm agent
    score: float = 0.0  # share of hidden test methods that passed (1.0 when passed)
    escalations: int = 0
    blocked: int = 0
    error: str = ""
    detail: str = ""
    workspace: str = ""


@dataclass
class Report:
    runs: list[RunResult] = field(default_factory=list)

    def summary(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for m in MODES:
            rs = [r for r in self.runs if r.mode == m]
            if rs:
                out[m] = {"runs": len(rs), "passRate": round(sum(r.passed for r in rs) / len(rs), 2),
                          "meanScore": round(statistics.mean(r.score for r in rs), 3),
                          "meanTokens": int(statistics.mean(r.tokens for r in rs)),
                          "meanCostUsd": round(statistics.mean(r.cost_usd for r in rs), 3),
                          "meanWallS": round(statistics.mean(r.wall_s for r in rs), 1),
                          "escalations": sum(r.escalations for r in rs), "blocked": sum(r.blocked for r in rs)}
        return out

    def markdown(self) -> str:
        rows = ["| mode | runs | pass rate | mean score | mean tokens | mean cost | mean wall | escalations | blocked |", "|---|---|---|---|---|---|---|---|---|"]
        for m, s in self.summary().items():
            rows.append(f"| {m} | {s['runs']} | {s['passRate']:.0%} | {s['meanScore']:.0%} | {tokens.fmt(s['meanTokens'])} | ${s['meanCostUsd']:.3f} | {s['meanWallS']}s | {s['escalations']} | {s['blocked']} |")
        det = ["", "| task | mode | pass | score | tokens | cost | wall | error |", "|---|---|---|---|---|---|---|---|"]
        for r in self.runs:
            det.append(f"| {r.task} | {r.mode} | {'yes' if r.passed else 'NO'} | {r.score:.0%} | {tokens.fmt(r.tokens)} | ${r.cost_usd:.3f} | {r.wall_s:.0f}s | {(r.error or r.detail)[:80]} |")
        return "\n".join(rows + det) + "\n"

    def to_json(self) -> str:
        return json.dumps({"summary": self.summary(), "runs": [asdict(r) for r in self.runs]}, indent=2)


def load_tasks(root: Path, only: list[str] | None = None) -> list[EvalTask]:
    tasks = []
    for d in sorted((root / "tasks").iterdir()) if (root / "tasks").is_dir() else []:
        f = d / "task.json"
        if not f.is_file():
            continue
        j = json.loads(f.read_text())
        if only and j["id"] not in only:
            continue
        tasks.append(EvalTask(j["id"], j["prompt"], j.get("tools", []), int(j.get("timeout_s", 1200)), d))
    return tasks


def _git(cwd: Path, *a: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_AUTHOR_NAME="eval", GIT_AUTHOR_EMAIL="eval@localhost", GIT_COMMITTER_NAME="eval", GIT_COMMITTER_EMAIL="eval@localhost")
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, env=env)


def prepare(task: EvalTask, dest: Path) -> Path:
    """Fresh git repo from the task's starting files (the hidden check is NOT included)."""
    dest.mkdir(parents=True)
    src = task.dir / "repo"
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    _git(dest, "init", "-q", "-b", "main")
    if not any(p for p in dest.iterdir() if p.name != ".git"):
        (dest / ".gitkeep").write_text("")
    _git(dest, "add", "-A"); _git(dest, "commit", "-qm", "eval start")
    return dest


# Runs the hidden tests and prints {"ran", "failed", "last"}: failed counts test METHODS (a failing subTest fails its
# method once), so ran - failed is the number of passing tests and the score is a fair pass share.
CHECK_SCRIPT = r"""
import json, sys, unittest
class R(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.bad = set()
    def addFailure(self, t, e): super().addFailure(t, e); self.bad.add(t.id())
    def addError(self, t, e): super().addError(t, e); self.bad.add(t.id())
    def addSubTest(self, t, st, e):
        super().addSubTest(t, st, e)
        if e is not None: self.bad.add(t.id())
    def addUnexpectedSuccess(self, t): super().addUnexpectedSuccess(t); self.bad.add(t.id())
suite = unittest.defaultTestLoader.discover(sys.argv[1], pattern="test_*.py")
r = unittest.TextTestRunner(resultclass=R, verbosity=0, stream=sys.stderr).run(suite)
last = (r.errors + r.failures)[-1][1].strip().splitlines()[-1] if (r.errors or r.failures) else ""
print(json.dumps({"ran": r.testsRun, "failed": len(r.bad), "last": last[:300]}))
"""


def check(task: EvalTask, repo: Path) -> tuple[bool, str, float]:
    """Copy the hidden tests in only now and run them with stdlib unittest (no dependencies).
    Returns (all passed, detail, score = share of hidden test methods that passed)."""
    chk = repo / "_eval_check"
    shutil.copytree(task.dir / "check", chk, dirs_exist_ok=True)
    try:
        p = subprocess.run([sys.executable, "-c", CHECK_SCRIPT, str(chk)],
                           cwd=repo, env=dict(os.environ, PYTHONPATH=str(repo), PYTHONDONTWRITEBYTECODE="1"),
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "hidden check timed out", 0.0
    finally:
        shutil.rmtree(chk, ignore_errors=True)
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        tail = (p.stderr or p.stdout).strip().splitlines()
        return False, (tail[-1] if tail else "hidden check crashed"), 0.0
    ran, failed = res["ran"], res["failed"]
    score = round((ran - failed) / ran, 3) if ran else 0.0
    ok = ran > 0 and failed == 0 and p.returncode == 0
    detail = "OK" if ok else f"{ran - failed}/{ran} hidden tests passed" + (f"; last: {res['last']}" if res["last"] else "")
    return ok, detail, score


def stay_awake() -> tuple[subprocess.Popen | None, str]:
    """macOS: keep the machine from idle-sleeping while the eval runs (a sleep cuts agents off mid-response and
    skews wall time). Returns (caffeinate process or None, a warning to print or '')."""
    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return None, ""
    proc = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        batt = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        batt = ""
    warn = ("warning: on battery power. macOS may still sleep (always with the lid closed), which interrupts runs; "
            "plug in and keep the lid open.") if "Battery Power" in batt else ""
    return proc, warn


def single_command(task: EvalTask, claude: str, model: str, budget: float) -> list[str]:
    return [claude, "-p", task.prompt, "--model", model, "--output-format", "json", "--permission-mode", "dontAsk",
            "--max-budget-usd", str(budget), "--allowedTools", "Read", "Glob", "Grep", "Write", "Edit", "Bash(git:*)", *task.tools]


def swarm_command(task: EvalTask, mode: str, claude: str, model: str, budget: float, plugin_dir: Path) -> list[str]:
    extras = []
    if mode in ("swarm+critics", "swarm+all"):
        extras.append("Call swarm_start with critics=true, and run critique_plan on your plan before launching workers.")
    if mode in ("swarm+research", "swarm+all"):
        extras.append(RESEARCH_HINT)
    prompt = f"/todo:todo {task.prompt}\n\n{EVAL_RULES} {' '.join(extras)}".strip()
    return [claude, "-p", prompt, "--model", model, "--output-format", "json", "--permission-mode", "dontAsk",
            "--max-budget-usd", str(budget), "--plugin-dir", str(plugin_dir),
            "--allowedTools", "Read", "Glob", "Grep", "Skill", "mcp__plugin_todo_swarm", "mcp__swarm"]


def _json_cost(stdout: str) -> tuple[float, str, int]:
    """(cost, error, tokens input + output) of a `claude -p --output-format json` result."""
    try:
        d = json.loads(stdout)
        return (float(d.get("total_cost_usd") or 0), "" if not d.get("is_error") else str(d.get("result", ""))[:200],
                tokens.view(**tokens.parse_usage(d))["total"])
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return 0.0, f"unparseable output: {stdout[:120]}", 0


def run_one(task: EvalTask, mode: str, workdir: Path, *, executor: Executor = subprocess_executor, claude: str = "claude",
            model: str = "sonnet", max_cost: float = 3.0, plugin_dir: Path = PROJECT_ROOT,
            max_tokens: int = DEFAULT_MAX_TOKENS) -> RunResult:
    repo = prepare(task, workdir / f"{task.id}-{mode.replace('+', '_')}")
    t0 = time.monotonic()
    env = dict(os.environ, SWARM_CLAUDE_BIN=claude, SWARM_HOME=str(repo), SWARM_TOKEN_CEILING=str(max_tokens))
    if mode == "single":
        cmd = single_command(task, claude, model, max_cost)
    else:
        cmd = swarm_command(task, mode, claude, model, max_cost, plugin_dir)
        env.pop("SWARM_CRITIC_MODEL", None)  # the mode decides, not the caller's environment
    ex = executor(cmd, repo, env, float(task.timeout_s))
    cost, err, used = _json_cost(ex.stdout)
    if ex.returncode == 124:
        err = err or ex.stderr
    escalations = blocked = 0
    if mode != "single" and (repo / ".swarm" / "swarm.db").is_file():
        try:
            board = Board(repo / ".swarm" / "swarm.db")
            with board._conn() as c:
                sid = c.execute("SELECT id FROM swarms ORDER BY created_at DESC LIMIT 1").fetchone()
                if sid:
                    r = c.execute("SELECT COALESCE(SUM(cost_usd),0), COALESCE(SUM(tokens_in+tokens_out+tokens_cache_write),0) FROM workers WHERE swarm_id=?", (sid[0],)).fetchone()
                    cost += r[0]; used += r[1]
                    escalations = c.execute("SELECT COUNT(*) FROM tasks WHERE swarm_id=? AND status='escalated'", (sid[0],)).fetchone()[0]
                    blocked = c.execute("SELECT COUNT(*) FROM events WHERE swarm_id=? AND kind='blocked'", (sid[0],)).fetchone()[0]
            ap = Integrator(Gate(Runner(board, repo))).apply()  # the harness, not the coordinator, applies the integration branch
            if not ap.get("ok"):
                err = err or f"integration not applied: {ap.get('error')}"
        except Exception as e:  # a broken swarm db is a failed run, not a crashed eval
            err = err or f"could not read swarm state: {e}"
    ok, detail, score = check(task, repo)
    return RunResult(task.id, mode, ok, round(cost, 4), round(time.monotonic() - t0, 1), used, score, escalations, blocked, err, detail, str(repo))


def run_eval(root: Path, tasks: list[EvalTask], modes: list[str], repeat: int, workdir: Path | None = None, **kw) -> Report:
    work = workdir or Path(tempfile.mkdtemp(prefix="todo-eval-"))
    rep = Report()
    for i in range(repeat):
        for t in tasks:
            for m in modes:
                rep.runs.append(run_one(t, m, work / f"r{i}", **kw))
    return rep


def worst_case(tasks: list[EvalTask], modes: list[str], repeat: int, max_cost: float, max_tokens: int = DEFAULT_MAX_TOKENS) -> tuple[int, float]:
    """Every run hits every cap: the top-level agent its dollar cap, a swarm's agents the token ceiling (priced at the measured rate)."""
    per = {m: max_cost + (0 if m == "single" else max_tokens / 1e6 * USD_PER_MILLION_TOKENS) for m in modes}
    n = len(tasks) * len(modes) * repeat
    return n, round(len(tasks) * repeat * sum(per.values()), 2)
