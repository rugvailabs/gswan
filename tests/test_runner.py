import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from swarmd import guard
from swarmd.board import Board
from swarmd.models import WorkerSpec
from swarmd.runner import Exec, Runner, RunnerError
from swarmd.workers import WorkerError


def git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    git(root, "init", "-q", "-b", "main"); git(root, "config", "user.email", "t@t"); git(root, "config", "user.name", "t")
    (root / "README").write_text("x"); git(root, "add", "."); git(root, "commit", "-qm", "init")
    (root / ".swarm").mkdir()
    return root


class FakeClaude:
    """Records calls; behaves like `claude -p` JSON output."""
    def __init__(self, delay=0.0, fail=False, denials=()):
        self.calls, self.delay, self.fail, self.denials = [], delay, fail, list(denials)
        self.active = 0; self.max_active = 0; self.lock = threading.Lock()

    def __call__(self, cmd, cwd, env, timeout):
        with self.lock:
            self.active += 1; self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        self.calls.append({"cmd": cmd, "cwd": Path(cwd), "env": env})
        with self.lock:
            self.active -= 1
        if self.fail:
            return Exec(1, json.dumps({"is_error": True, "result": "boom"}), "")
        sid = cmd[cmd.index("--session-id") + 1] if "--session-id" in cmd else cmd[cmd.index("--resume") + 1]
        return Exec(0, json.dumps({"result": f"ok:{cmd[2][:30]}", "session_id": sid, "total_cost_usd": 0.01,
                                   "is_error": False, "permission_denials": [{"tool_name": t} for t in self.denials]}), "")


def make(repo, tmp_path, **kw):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    fake = FakeClaude(**kw)
    return board, Runner(board, repo, executor=fake), fake


SPEC = dict(name="api", goal="build api", owns=["src/api"])


def test_define_and_duplicate(repo, tmp_path):
    board, r, _ = make(repo, tmp_path)
    w = r.define(WorkerSpec(**SPEC))
    assert w.id.startswith("wrk_") and w.status == "idle" and w.session_id
    with pytest.raises(WorkerError):
        r.define(WorkerSpec(**SPEC))
    assert any("worker api" in e.body for e in board.list_events().items)


def test_spec_validation():
    for bad in (dict(name="Bad Name", goal="g", owns=["a"]), dict(name="ok", goal="g", owns=[])):
        with pytest.raises(ValueError):
            WorkerSpec(**bad)


def test_first_turn_session_id_then_resume_and_worktree(repo, tmp_path):
    board, r, fake = make(repo, tmp_path); r.define(WorkerSpec(**SPEC))
    res = r.invoke("api", "start")
    assert res.ok and res.turns_run == 1 and not res.queued_only
    c1 = fake.calls[0]
    assert "--session-id" in c1["cmd"] and "--resume" not in c1["cmd"]
    assert c1["cwd"] == repo / ".swarm" / "wt" / "api" and c1["cwd"].is_dir()
    r.invoke("api", "continue")
    c2 = fake.calls[1]
    assert "--resume" in c2["cmd"] and c2["cmd"][c2["cmd"].index("--resume") + 1] == res.session_id
    w = r.reg.get("api")
    assert w.turns == 2 and w.status == "idle" and w.cost_usd == pytest.approx(0.02) and w.pid is None
    assert w.branch.startswith("swarm/swm_")


def test_command_flags_and_env(repo, tmp_path):
    board, r, fake = make(repo, tmp_path); r.define(WorkerSpec(**SPEC, tools=["Bash(pytest:*)"]))
    r.invoke("api", "go"); c = fake.calls[0]; cmd = c["cmd"]
    assert cmd[:3] == ["claude", "-p", "go"] and cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert Path(cmd[cmd.index("--settings") + 1]).is_absolute()
    allowed = cmd[cmd.index("--allowedTools") + 1: cmd.index("--session-id")]
    assert "Write" in allowed and not any(t.startswith("Bash") for t in allowed)  # Bash is decided by the policy hook
    assert json.loads(c["env"]["SWARM_ALLOW"]) == ["Bash(pytest:*)"] and c["env"]["SWARM_ROLE"] == "worker"
    assert c["env"]["PYTHONDONTWRITEBYTECODE"] == "1"  # no stray .pyc in worker commits
    assert c["env"]["SWARM_BRANCH_PREFIX"].startswith("swarm/swm_") and c["env"]["SWARM_WORKTREE"].endswith("/.swarm/wt/api")
    assert "worker 'api'" in cmd[cmd.index("--append-system-prompt") + 1]
    assert c["env"]["SWARM_WORKER"] == "api" and c["env"]["SWARM_HOME"] == str(repo)
    assert c["env"]["SWARM_OWNS"] == str((repo / ".swarm/wt/api/src/api").resolve())
    hooks = json.loads((repo / ".swarm/hooks.json").read_text())
    assert "swarmd.guard" in hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "Bash" in hooks["hooks"]["PreToolUse"][0]["matcher"]


def test_mentions_are_posted_and_denials_reported(repo, tmp_path):
    board, r, _ = make(repo, tmp_path, denials=["Bash"]); r.define(WorkerSpec(**SPEC))
    res = r.invoke("api", "do it", sender="human")
    assert res.denials == ["Bash"]  # fake provides no tool_input detail
    bodies = [(e.agent, e.kind, e.body) for e in board.list_events().items]
    assert ("human", "mention", "@api do it") in bodies
    assert any(a == "swarmd" and "denied: Bash" in b for a, _, b in bodies)


def test_failure_marks_error_and_posts_blocked(repo, tmp_path):
    board, r, _ = make(repo, tmp_path, fail=True); r.define(WorkerSpec(**SPEC))
    res = r.invoke("api", "go")
    assert not res.ok and r.reg.get("api").status == "error" and r.reg.get("api").last_error
    assert any(e.kind == "blocked" and "turn failed" in e.body for e in board.list_events().items)
    # error status must not block a later retry
    assert r.reg.try_claim(r.reg.get("api"))


def test_no_commit_repo_gives_clear_error(tmp_path):
    root = tmp_path / "empty"; root.mkdir(); git(root, "init", "-q", "-b", "main"); (root / ".swarm").mkdir()
    board, r, _ = make(root, tmp_path); r.define(WorkerSpec(**SPEC))
    res = r.invoke("api", "go")
    assert not res.ok and "no commits yet" in res.text


def test_unknown_worker(repo, tmp_path):
    _, r, _ = make(repo, tmp_path)
    with pytest.raises(WorkerError):
        r.invoke("ghost", "hi")


def test_concurrent_mentions_serialize_per_worker_and_merge(repo, tmp_path):
    board, r, fake = make(repo, tmp_path, delay=0.4); r.define(WorkerSpec(**SPEC))
    results = []
    ts = [threading.Thread(target=lambda i=i: results.append(r.invoke("api", f"m{i}", sender=f"s{i}"))) for i in range(4)]
    for t in ts:
        t.start(); time.sleep(0.05)
    [t.join() for t in ts]
    assert fake.max_active == 1  # never two turns for the same worker at once
    sent = " ".join(c["cmd"][2] for c in fake.calls)
    assert all(f"m{i}" in sent for i in range(4))  # nothing lost
    assert len(fake.calls) < 4  # later mentions were merged into one turn
    assert sum(1 for x in results if x.queued_only) >= 1
    assert r.reg.queue_len(r.reg.get("api")) == 0 and r.reg.get("api").status == "idle"


def test_different_workers_run_in_parallel(repo, tmp_path):
    board, r, fake = make(repo, tmp_path, delay=0.5)
    r.define(WorkerSpec(**SPEC)); r.define(WorkerSpec(name="ui", goal="g", owns=["src/ui"]))
    ts = [threading.Thread(target=r.invoke, args=(n, "go")) for n in ("api", "ui")]
    t0 = time.time(); [t.start() for t in ts]; [t.join() for t in ts]
    assert fake.max_active == 2 and time.time() - t0 < 0.95
    assert {c["cwd"].name for c in fake.calls} == {"api", "ui"}


def test_dead_pid_claim_is_recoverable(repo, tmp_path):
    board, r, _ = make(repo, tmp_path); w = r.define(WorkerSpec(**SPEC))
    assert r.reg.try_claim(w, pid=2_000_000)  # pretend a crashed process held it
    assert r.reg.try_claim(w)  # dead pid -> reclaimable
    r.reg.release(w)


def test_live_pid_blocks_claim(repo, tmp_path):
    board, r, _ = make(repo, tmp_path); w = r.define(WorkerSpec(**SPEC))
    assert r.reg.try_claim(w, pid=os.getppid())
    assert not r.reg.try_claim(w, pid=os.getpid())


# ---- guard hook ------------------------------------------------------------
ENV = {"SWARM_WORKER": "api", "SWARM_OWNS": os.pathsep.join(["/w/src/api", "/w/docs/x.md"])}


@pytest.mark.parametrize("path,allowed", [
    ("/w/src/api/a.py", True), ("/w/src/api/deep/b.py", True), ("/w/docs/x.md", True),
    ("/w/src/apix/a.py", False), ("/w/src/ui/a.py", False), ("/w/src/api/../ui/a.py", False), ("", False)])
def test_guard_paths(path, allowed):
    out = guard.decide({"tool_name": "Write", "tool_input": {"file_path": path}}, ENV)
    assert (out is None) == allowed
    if not allowed:
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_guard_ignores_reads_and_non_swarm_sessions():
    assert guard.decide({"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}, ENV) is None
    assert guard.decide({"tool_name": "Write", "tool_input": {"file_path": "/etc/x"}}, {}) is None


def test_node_modules_symlinked_into_worktree_when_ignored(repo, tmp_path):
    (repo / ".gitignore").write_text("node_modules\n"); git(repo, "add", "."); git(repo, "commit", "-qm", "ignore")
    (repo / "node_modules").mkdir(); (repo / "node_modules" / "pkg").write_text("x")
    board, r, fake = make(repo, tmp_path); r.define(WorkerSpec(**SPEC)); r.invoke("api", "go")
    link = repo / ".swarm" / "wt" / "api" / "node_modules"
    assert link.is_symlink() and (link / "pkg").read_text() == "x"


def test_node_modules_not_linked_when_not_ignored(repo, tmp_path):
    (repo / "node_modules").mkdir()
    board, r, fake = make(repo, tmp_path); r.define(WorkerSpec(**SPEC)); r.invoke("api", "go")
    assert not (repo / ".swarm" / "wt" / "api" / "node_modules").exists()


def test_worker_reply_is_posted_to_the_board_with_a_reply_ref(repo, tmp_path):
    board, r, _ = make(repo, tmp_path); r.define(WorkerSpec(**SPEC)); r.invoke("api", "go")
    evs = [e for e in board.list_events().items if "reply:api" in e.refs]
    assert len(evs) == 1 and evs[0].agent == "swarmd" and evs[0].body.startswith("api replied: ok:")
