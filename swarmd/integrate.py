"""Integration: merge verified worker branches into `swarm/<id>/integration` (a dedicated worktree), never into the
user's checked-out branch. Conflicts are left in the integration worktree and handed to an `integrator` worker
(owns the whole integration worktree, verified like any other worker). A human applies the result with `--apply`."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from .gate import Gate
from .models import EventIn, WorkerSpec
from .workers import WorkerError


class IntegrationError(Exception):
    pass


class Integrator:
    def __init__(self, gate: Gate):
        self.gate, self.runner, self.board = gate, gate.runner, gate.board
        self.reg, self.root = self.runner.reg, self.runner.root
        self.last_thread: threading.Thread | None = None

    # ---- git helpers ---------------------------------------------------------------
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if subprocess.run(["git", "-C", str(self.root), "config", "user.email"], capture_output=True, text=True).stdout.strip() == "":
            env.update(GIT_AUTHOR_NAME="swarm", GIT_AUTHOR_EMAIL="swarm@localhost",
                       GIT_COMMITTER_NAME="swarm", GIT_COMMITTER_EMAIL="swarm@localhost")
        return env

    def _git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=self._env())

    def paths(self) -> tuple[Path, str]:
        sw = self.board._require()
        return self.root / ".swarm" / "wt" / "_integration", f"swarm/{sw.id}/integration"

    def ensure(self) -> tuple[Path, str]:
        wt, branch = self.paths()
        if wt.is_dir():
            return wt, branch
        workers = [w for w in self.reg.list() if w.name != "integrator"]
        base = next((w.base_sha for w in workers if w.base_sha), None)
        if base is None:
            raise IntegrationError("no worker has run yet, so there is nothing to integrate")
        r = self._git(self.root, "worktree", "add", "-q", str(wt), "-b", branch, base)
        if r.returncode != 0:
            raise IntegrationError(f"could not create the integration worktree: {r.stderr.strip()}")
        return wt, branch

    def _merging(self, wt: Path) -> bool:
        return self._git(wt, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0

    def _unmerged(self, wt: Path) -> list[str]:
        return [x for x in self._git(wt, "diff", "--name-only", "--diff-filter=U").stdout.split("\n") if x]

    # ---- integrator worker ---------------------------------------------------------
    def _integrator(self, wt: Path, branch: str):
        try:
            return self.reg.get("integrator")
        except WorkerError:
            pass
        workers = [w for w in self.reg.list()]
        tools = sorted({t for w in workers for t in w.tools})
        w = self.reg.define(WorkerSpec(name="integrator", goal="resolve merge conflicts between worker branches in the integration worktree and keep the combined code working",
                                       owns=["."], tools=tools, model="sonnet"), exempt_limit=True)
        self.reg.set_worktree(w, str(wt), branch)
        base = next((x.base_sha for x in workers if x.base_sha), None)
        if base:
            self.reg.set_base(w, base)
        return self.reg.get("integrator")

    def _launch_integrator(self, wt: Path, branch: str, worker: str, files: list[str]) -> None:
        integ = self._integrator(wt, branch)
        goals = "\n".join(f"- {w.name}: {w.goal}" for w in self.reg.list() if w.name != "integrator")
        from .tasks import Tasks
        Tasks(self.board).set_status("integrator", "open", only_from=("done", "failed"))
        msg = (f"Merging branch of worker '{worker}' into the integration branch produced conflicts in: {', '.join(files)}.\n"
               f"The merge is in progress in your working directory. Resolve EVERY conflict marker, keeping the intent of both sides.\n"
               f"Worker goals:\n{goals}\n"
               f"Then run the allowed test commands if any, `git add` the resolved files, run `git commit --no-edit`, and post `done` "
               f"(files resolved, how each was resolved, what you ran).")
        self.board.post(EventIn(agent="swarmd", kind="update", body=f"merge conflict in {', '.join(files)} ({worker}); integrator resolving"))
        th = threading.Thread(target=lambda: self.gate.invoke("integrator", msg, "swarmd"), name="integrator", daemon=True)
        th.start()
        self.last_thread = th

    # ---- main entry ----------------------------------------------------------------
    def merge_ready(self, auto_resolve: bool = True, order: list[str] | None = None) -> dict:
        from .tasks import Tasks
        wt, branch = self.ensure()
        tasks, merged, pending, conflicts = Tasks(self.board), [], [], []
        out = {"ok": True, "branch": branch, "worktree": str(wt), "merged": merged, "pending": pending,
               "conflicts": conflicts, "integratorStarted": False, "resolving": False}
        if self._merging(wt):  # an earlier conflict is still being resolved
            out["resolving"] = True
            out["conflicts"] = [{"worker": "(in progress)", "files": self._unmerged(wt)}]
            return out
        workers = {w.name: w for w in self.reg.list() if w.name != "integrator"}
        names = [n for n in (order or sorted(workers)) if n in workers]
        for n in names:
            w = workers[n]
            if tasks.get(n).status != "done" or not w.branch:
                pending.append({"worker": n, "task": tasks.get(n).status})
                continue
            if self._git(wt, "merge-base", "--is-ancestor", w.branch, "HEAD").returncode == 0:
                merged.append(n)
                continue
            r = self._git(wt, "merge", "--no-ff", "-m", f"swarm: merge {n}: {w.goal[:60]}", w.branch)
            if r.returncode == 0:
                merged.append(n)
                self.board.post(EventIn(agent="swarmd", kind="update", body=f"merged {n} into {branch}"))
                continue
            files = self._unmerged(wt)
            if not files:  # failed for another reason (unrelated histories, dirty tree...)
                self._git(wt, "merge", "--abort")
                out["ok"] = False
                out["error"] = f"merge of {n} failed: {(r.stderr or r.stdout).strip()[:300]}"
                return out
            conflicts.append({"worker": n, "files": files})
            if auto_resolve:
                self._launch_integrator(wt, branch, n, files)
                out["integratorStarted"] = True
            break  # stop at the first conflict; call again after it is resolved
        out["allMerged"] = not pending and not conflicts and len(merged) == len(names)
        if out["allMerged"]:
            out["applyHint"] = f"git merge --no-ff {branch}   (or: swarm integrate --apply)"
        return out

    def verify_integration(self) -> threading.Thread:
        """Run the normal verifier over the assembled integration branch (granted test commands only, read-only)."""
        wt, branch = self.ensure()
        self._integrator(wt, branch)
        from .tasks import Tasks
        Tasks(self.board).set_status("integrator", "open", only_from=("done", "failed", "escalated"))
        self.board.post(EventIn(agent="integrator", kind="done",
                                body=f"Integration branch {branch} assembled from all verified worker branches. "
                                     f"Verify it as a whole: run the granted test commands and confirm the combined code works."))
        th = threading.Thread(target=lambda: self.gate.settle("integrator"), name="verify-integration", daemon=True)
        th.start()
        self.last_thread = th
        return th

    def apply(self) -> dict:
        """Human action: merge the integration branch into the repo's current branch (refuses on a dirty tree)."""
        wt, branch = self.paths()
        if not wt.is_dir():
            return {"ok": False, "error": "nothing integrated yet"}
        if self._git(self.root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            return {"ok": False, "error": "your working tree has uncommitted changes; commit or stash them first"}
        r = self._git(self.root, "merge", "--no-ff", "-m", f"swarm: apply {branch}", branch)
        if r.returncode != 0:
            self._git(self.root, "merge", "--abort")
            return {"ok": False, "error": f"apply failed (aborted cleanly): {(r.stderr or r.stdout).strip()[:300]}"}
        return {"ok": True, "applied": branch}
