"""Verifier gate: a worker's `done` is provisional. After each worker turn, run a read-only verifier;
failures go back to the worker (max MAX_ROUNDS), then escalate to the human."""
from __future__ import annotations

from .board import Board
from .models import EventIn, TurnResult
from .runner import Runner
from .tasks import MAX_ROUNDS, Tasks

MAX_NO_VERDICT = 2  # verifier turns that end without a verdict before escalating


class Gate:
    def __init__(self, runner: Runner, verifier_model: str = "sonnet"):
        self.runner, self.board = runner, runner.board
        self.tasks, self.verifier_model = Tasks(runner.board), verifier_model

    def invoke(self, name: str, message: str, sender: str = "coordinator") -> TurnResult:
        """@mention a worker, then verify/iterate until its task settles."""
        res = self.runner.invoke(name, message, sender=sender)
        if res.queued_only or not res.ok:
            return res
        self.settle(name)
        return res

    def drain(self, name: str) -> TurnResult | None:
        """Run whatever is queued for a worker (e.g. peer mentions), then verify/iterate."""
        res = self.runner.run_queue(name)
        if res is not None and res.ok:
            self.settle(name)
        return res

    def settle(self, name: str) -> None:
        no_verdict = 0
        while True:
            t = self.tasks.get(name)
            if t.status != "awaiting_verification":
                return
            if not self.tasks.set_status(name, "verifying", only_from=("awaiting_verification",)):
                return  # someone else is verifying it
            target = self.runner.reg.get(name)
            v = self.runner.reg.define_verifier(self.runner.reg.get(name), self.verifier_model)
            if not v.cwd:  # worktree path becomes known after the worker's first turn
                self.runner.reg.set_cwd(v, target.worktree)
            self.board.post(EventIn(agent="swarmd", kind="update", body=f"verifying {name} (round {t.rounds + 1})"))
            res = self.runner.invoke(v.name, self.brief(target, t), sender="swarmd")
            t = self.tasks.get(name)
            if t.status == "verifying":  # verifier ended its turn without a verdict
                no_verdict += 1
                self.tasks.set_status(name, "awaiting_verification")
                if no_verdict >= MAX_NO_VERDICT or not res.ok:
                    self.tasks.set_status(name, "escalated")
                    self.board.post(EventIn(agent="swarmd", kind="blocked",
                                            body=f"verifier for {name} gave no verdict ({res.text[:200]}); needs a human"))
                    return
                continue
            if t.status == "done":
                return
            if t.status == "failed":
                if t.rounds >= MAX_ROUNDS:
                    self.tasks.set_status(name, "escalated")
                    self.board.post(EventIn(agent="swarmd", kind="blocked",
                                            body=f"{name} failed verification {t.rounds} times; needs a human. Last reasons: " + " | ".join(t.reasons)))
                    return
                fix = self.runner.invoke(name, self.fix_message(t), sender=f"verify-{name}")
                if not fix.ok or fix.queued_only:
                    return
                # worker posts `done` again -> task flips to awaiting_verification -> loop verifies again

    @staticmethod
    def fix_message(t) -> str:
        return ("Verification FAILED (round %d). Fix every item, re-run your checks, commit, then post `done` again:\n" % t.rounds
                + "\n".join(f"{i}. {r}" for i, r in enumerate(t.reasons, 1)))

    def brief(self, w, t) -> str:
        done = [e for e in self.board.list_events(agent=w.name, kind="done", limit=500).items][-1:]
        summary = done[0].body if done else "(no done message found)"
        prior = f"\nPrevious failed reasons (check they are fixed): {' | '.join(t.reasons)}" if t.reasons else ""
        return (f"Verify worker '{w.name}' (task {t.id}).\nGoal: {t.goal}\nOwned paths: {', '.join(w.owns)}\n"
                f"Base commit: {w.base_sha or 'unknown'} (use `git diff {w.base_sha or 'HEAD~1'}`).\n"
                f"Worker's done message: {summary}{prior}\nGive your verdict with the board CLI `verdict {w.name} pass|fail`.")
