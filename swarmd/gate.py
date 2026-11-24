"""Verifier gate: a worker's `done` is provisional. After each worker turn, run a read-only verifier;
failures go back to the worker (max MAX_ROUNDS), then escalate to the human."""
from __future__ import annotations

import subprocess
import threading

from .board import Board
from .critics import MAX_CRITIC_ROUNDS, PERSONAS, Critiques, choose_personas, fmt
from .models import EventIn, TurnResult
from .runner import Runner
from .tasks import MAX_ROUNDS, Tasks

MAX_NO_VERDICT = 2  # verifier turns that end without a verdict before escalating


class Gate:
    def __init__(self, runner: Runner, verifier_model: str = "sonnet", critic_model: str | None = None,
                 personas: tuple[str, ...] = ("correctness", "security", "tests")):
        self.runner, self.board = runner, runner.board
        self.tasks, self.verifier_model = Tasks(runner.board), verifier_model
        self.critic_model, self.personas, self.crit = critic_model, personas, Critiques(runner.board)  # critic_model None = critics off

    def invoke(self, name: str, message: str, sender: str = "coordinator") -> TurnResult:
        """@mention a worker, then verify/iterate until its task settles."""
        res = self.runner.invoke(name, message, sender=sender)
        if res.queued_only or not res.ok or self.runner.reg.get(name).role != "worker":
            return res  # researchers change nothing, so there is nothing to verify
        self.settle(name)
        return res

    def drain(self, name: str) -> TurnResult | None:
        """Run whatever is queued for a worker (e.g. peer mentions), then verify/iterate."""
        res = self.runner.run_queue(name)
        if res is not None and res.ok and self.runner.reg.get(name).role == "worker":
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
            if self.critic_model and self.crit.rounds(name) < MAX_CRITIC_ROUNDS and target.worktree:
                blockers = self.run_critics(target, t)
                if blockers:  # the worker fixes them first; no verifier round is spent
                    self.tasks.set_status(name, "failed")
                    self.board.post(EventIn(agent="swarmd", kind="update",
                                            body=f"{len(blockers)} critic blocker(s) on {name} (round {self.crit.rounds(name)}); sent back to the worker", refs=[f"task:{name}:failed"]))
                    fix = self.runner.invoke(name, self.critic_fix_message(blockers), sender="critics")
                    if not fix.ok or fix.queued_only:
                        return
                    continue  # worker posts `done` again -> awaiting_verification -> next critic round or verifier
                majors = self.crit.majors(name, self.crit.rounds(name))
                if majors:  # once: the worker fixes or explains them, then the verifier (not another critic round) checks
                    self.crit.close(name)
                    self.tasks.set_status(name, "failed")
                    self.board.post(EventIn(agent="swarmd", kind="update",
                                            body=f"{len(majors)} critic major(s) on {name}; sent back once to fix or explain, then the verifier checks", refs=[f"task:{name}:failed"]))
                    fix = self.runner.invoke(name, self.critic_major_message(majors), sender="critics")
                    if not fix.ok or fix.queued_only:
                        return
                    continue
            v = self.runner.reg.define_verifier(self.runner.reg.get(name), self.verifier_model)
            if not v.cwd:  # worktree path becomes known after the worker's first turn
                self.runner.reg.set_cwd(v, target.worktree)
            self.board.post(EventIn(agent="swarmd", kind="update", body=f"verifying {name} (round {t.rounds + 1})", refs=[f"task:{name}:verifying"]))
            res = self.runner.invoke(v.name, self.brief(target, t), sender="swarmd")
            t = self.tasks.get(name)
            if t.status == "verifying":  # verifier ended its turn without a verdict
                no_verdict += 1
                self.tasks.set_status(name, "awaiting_verification")
                if no_verdict >= MAX_NO_VERDICT or not res.ok:
                    self.tasks.set_status(name, "escalated")
                    self.board.post(EventIn(agent="swarmd", kind="blocked",
                                            body=f"verifier for {name} gave no verdict ({res.text[:200]}); needs a human", refs=[f"task:{name}:escalated"]))
                    return
                continue
            if t.status == "done":
                return
            if t.status == "failed":
                if t.rounds >= MAX_ROUNDS:
                    self.tasks.set_status(name, "escalated")
                    self.board.post(EventIn(agent="swarmd", kind="blocked",
                                            body=f"{name} failed verification {t.rounds} times; needs a human. Last reasons: " + " | ".join(t.reasons), refs=[f"task:{name}:escalated"]))
                    return
                fix = self.runner.invoke(name, self.fix_message(t), sender=f"verify-{name}")
                if not fix.ok or fix.queued_only:
                    return
                # worker posts `done` again -> task flips to awaiting_verification -> loop verifies again

    def changes(self, w) -> list[tuple[str, int]] | None:
        """[(path, added + deleted lines)] the worker committed since its base, or None when git cannot tell."""
        if not (w.base_sha and w.worktree):
            return None
        r = subprocess.run(["git", "-C", w.worktree, "diff", "--numstat", f"{w.base_sha}..HEAD"], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        out = []
        for line in r.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                out.append((parts[2], sum(int(x) for x in parts[:2] if x.isdigit())))
        return out

    def run_critics(self, target, t) -> list:
        """One round: the chosen critics review the worker's diff in parallel (read-only). Returns this round's blockers."""
        personas, why = choose_personas(self.personas, self.changes(target))
        if not personas:
            self.crit.close(target.name)
            self.board.post(EventIn(agent="swarmd", kind="update", body=f"critics skipped for {target.name}: {why}; the verifier still checks it"))
            return []
        rnd = self.crit.begin_round(target.name)
        self.board.post(EventIn(agent="swarmd", kind="update", body=f"critics reviewing {target.name} (round {rnd}): {', '.join(personas)}" + (f" ({why})" if why else "")))
        threads = []
        for p in personas:
            c = self.runner.reg.define_critic(f"critic-{target.name}-{p}", f"{p}: look for {PERSONAS[p]}", cwd=target.worktree, model=self.critic_model)
            th = threading.Thread(target=self.runner.invoke, args=(c.name, self.critic_brief(target, t, p)), kwargs={"sender": "swarmd"}, daemon=True)
            th.start(); threads.append(th)
        for th in threads:
            th.join()
        return self.crit.blockers(target.name, rnd)

    def critic_brief(self, w, t, persona: str) -> str:
        prior = self.crit.list(w.name)
        again = ("\nEarlier findings (check whether they are fixed; report only what is still wrong or newly wrong):\n" + fmt(prior)) if prior else ""
        return (f"Critique worker '{w.name}'. Target for your findings: {w.name}\nGoal: {t.goal}\nOwned paths: {', '.join(w.owns)}\n"
                f"Base commit: {w.base_sha or 'unknown'} (use `git diff {w.base_sha or 'HEAD~1'}`). Your focus: {persona} - {PERSONAS[persona]}.{again}")

    @staticmethod
    def critic_fix_message(blockers) -> str:
        return ("Code critics found BLOCKERS. Fix each (or, if one is wrong, explain why in your done message with evidence), re-run your checks, "
                "commit, then post `done` again:\n" + fmt(blockers))

    @staticmethod
    def critic_major_message(majors) -> str:
        return ("Code critics found MAJOR weaknesses (no blockers). For each: fix it, or say in your done message why you did not "
                "(with evidence). Re-run your checks, commit, then post `done` again. No further critic round follows; the verifier "
                "checks your answer:\n" + fmt(majors))

    @staticmethod
    def fix_message(t) -> str:
        return ("Verification FAILED (round %d). Fix every item, re-run your checks, commit, then post `done` again:\n" % t.rounds
                + "\n".join(f"{i}. {r}" for i, r in enumerate(t.reasons, 1)))

    def brief(self, w, t) -> str:
        done = [e for e in self.board.list_events(agent=w.name, kind="done", limit=500).items][-1:]
        summary = done[0].body if done else "(no done message found)"
        prior = f"\nPrevious failed reasons (check they are fixed): {' | '.join(t.reasons)}" if t.reasons else ""
        open_b = self.crit.blockers(w.name, self.crit.rounds(w.name) or None) if self.critic_model else []
        crit = f"\nUnresolved critic blockers from the last round (confirm each is fixed or wrong, fail if real): {' | '.join(f.location + ' ' + f.reason for f in open_b)}" if open_b else ""
        majors = self.crit.majors(w.name) if self.critic_model else []
        maj = (f"\nCritic majors sent to the worker (check each was fixed or explained; fail only if one breaks the goal): "
               f"{' | '.join(f.location + ' ' + f.reason for f in majors)}") if majors else ""
        checks = (f"\nRequired checks: run EACH of these exactly (you may add flags at the end) and report its result; a pass verdict is "
                  f"refused until each has run: " + " ; ".join(f"`{c}`" for c in w.checks)) if w.checks else ""
        return (f"Verify worker '{w.name}' (task {t.id}).\nGoal: {t.goal}\nOwned paths: {', '.join(w.owns)}\n"
                f"Base commit: {w.base_sha or 'unknown'} (use `git diff {w.base_sha or 'HEAD~1'}`).\n"
                f"Worker's done message: {summary}{prior}{crit}{maj}{checks}\nGive your verdict with the board CLI `verdict {w.name} pass|fail`.")
