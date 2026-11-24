"""`swarm` CLI. Talks to the SQLite board directly, so it works with or without the daemon."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

from .board import Board, NoActiveSwarm
from . import tokens
from .models import EVENT_KINDS, Event, EventIn


def find_root(start: Path | None = None) -> Path:
    """Directory holding .swarm/: $SWARM_HOME, else nearest ancestor with .swarm/, else the git toplevel, else cwd."""
    if os.environ.get("SWARM_HOME"):
        return Path(os.environ["SWARM_HOME"])
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if (d / ".swarm").is_dir():
            return d
    top = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else p


def ensure_swarm_dir(root: Path) -> Path:
    """Create .swarm/ with a self-ignoring .gitignore so the swarm never dirties the user's repo."""
    d = root / ".swarm"
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n")
    return d


def open_board(root: Path) -> Board:
    d = root / ".swarm"
    return Board(d / "swarm.db", markdown_path=d / "board.md")


def fmt(e: Event) -> str:
    return f"#{e.seq} {e.ts[11:19]} {e.agent:<12} {e.kind:<8} {e.body}"


def cmd_init(b: Board, a) -> int:
    sw = b.start_swarm(a.task)
    print(sw.id)
    return 0


def cmd_post(b: Board, a) -> int:
    body = a.message if a.message != "-" else sys.stdin.read().strip()
    if a.kind == "critique":
        print("error: use `swarm critique <target> <severity> <reason> --at file:line` so findings are structured", file=sys.stderr)
        return 2
    if a.kind == "research":
        role = os.environ.get("SWARM_ROLE")
        if role and role != "researcher":
            print("error: only researchers post `research` events", file=sys.stderr)
            return 3
        if "UNVERIFIED" not in body and not re.search(r"https?://\S+", body):
            print("error: a research finding needs a source URL (https://...) and a quote, or must start with UNVERIFIED:", file=sys.stderr)
            return 2
    e = b.post(EventIn(agent=a.agent, kind=a.kind, body=body, task_id=a.task_id, refs=a.ref or []))
    print(f"#{e.seq} {e.id}")
    return 0


def cmd_show(b: Board, a) -> int:
    page = b.list_events(limit=500)
    items = page.items[-a.n:] if a.n else page.items
    if a.json:
        print(json.dumps([e.model_dump(by_alias=True) for e in items], indent=2))
    else:
        print("\n".join(fmt(e) for e in items))
    return 0


def cmd_tail(b: Board, a) -> int:
    seq = max(0, b.last_seq() - a.n)
    try:
        while True:
            for e in b.events_after(seq):
                seq = e.seq
                print(fmt(e), flush=True)
            if a.once:
                return 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def cmd_digest(b: Board, a) -> int:
    d = b.digest(a.since)
    print(json.dumps(d.model_dump(by_alias=True), indent=2) if a.json else d.text)
    return 0


def cmd_stale(b: Board, a) -> int:
    rows = b.stale(a.minutes)
    for s in rows:
        print(f"STALE {s.agent} (last {s.last_kind} at {s.last_ts}, idle {s.idle_minutes}m)")
    return 1 if rows and a.fail else 0


def cmd_export(b: Board, a) -> int:
    out = b.markdown()
    if a.path:
        Path(a.path).write_text(out)
    else:
        print(out, end="")
    return 0


def cmd_verdict(b: Board, a) -> int:
    from .tasks import TaskError, Tasks
    if os.environ.get("SWARM_ROLE") != "verifier" and not a.force:
        print("error: only a verifier may give verdicts (humans: pass --force)", file=sys.stderr)
        return 3
    who = os.environ.get("SWARM_WORKER") if os.environ.get("SWARM_ROLE") == "verifier" else "human"
    if who != "human" and who != f"verify-{a.worker}":
        print(f"error: {who} may only verify '{who.removeprefix('verify-')}'", file=sys.stderr)
        return 3
    if who != "human" and a.verdict == "pass":
        from .workers import Registry, WorkerError
        try:
            missing = Tasks(b).missing_checks(a.worker, Registry(b).get(who).checks, who)
        except (WorkerError, TaskError):
            missing = []
        if missing:
            print("error: pass refused: run these required checks first, then give the verdict again: "
                  + " ; ".join(missing), file=sys.stderr)
            return 2
    try:
        t = Tasks(b).submit_verdict(a.worker, a.verdict == "pass", a.reason or [], verifier=who)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{t.worker}: {t.status}")
    return 0


def cmd_critique(b: Board, a) -> int:
    from .critics import Critiques, CritiqueError
    role = os.environ.get("SWARM_ROLE")
    if role != "critic" and not a.force:
        print("error: only critics post critiques (humans: pass --force)", file=sys.stderr)
        return 3
    who = os.environ.get("SWARM_WORKER") if role == "critic" else "human"
    if a.severity == "ok":
        b.post(EventIn(agent=who, kind="critique", body=f"ok {a.target}: {a.reason}"[:7900]))
        print("recorded: no findings")
        return 0
    try:
        f = Critiques(b).add(a.target, who, a.severity, a.reason, a.at or "")
    except CritiqueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"finding {f.id}: {f.severity}")
    return 0


def cmd_eval(a, root: Path) -> int:
    from . import evals
    base = Path(a.dir) if a.dir else evals.PROJECT_ROOT / "evals"
    tasks = evals.load_tasks(base, a.task)
    modes = a.mode or list(evals.MODES)
    bad = [m for m in modes if m not in evals.MODES]
    if bad or not tasks:
        print(f"error: {'unknown mode(s) ' + ', '.join(bad) if bad else 'no tasks found in ' + str(base / 'tasks')}", file=sys.stderr)
        return 2
    n, worst = evals.worst_case(tasks, modes, a.repeat, a.max_cost)
    print(f"{len(tasks)} task(s) x {len(modes)} mode(s) x {a.repeat} = {n} real run(s); per-run cap ${a.max_cost:.2f} (swarm modes bill workers too); rough worst case ${worst:.0f}")
    if not a.yes:
        print("dry run: nothing executed. Re-run with --yes to spend that money.")
        return 0
    rep = evals.run_eval(base, tasks, modes, a.repeat, claude=os.environ.get("SWARM_CLAUDE_BIN", "claude"), model=a.model, max_cost=a.max_cost)
    out = base / "results"; out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(rep.to_json()); (out / f"{stamp}.md").write_text(rep.markdown())
    print(rep.markdown()); print(f"saved {out / (stamp + '.md')}")
    return 0 if all(r.passed for r in rep.runs) else 1


def cmd_report(b: Board, a, root: Path) -> int:
    from . import report
    d = report.build(b, root)
    out = report.as_json(d) if a.json else report.markdown(d)
    if a.out:
        Path(a.out).write_text(out)
        print(f"wrote {a.out}")
    else:
        print(out, end="")
    return 0


def cmd_monitor(b: Board, a, root: Path) -> int:
    from .gate import Gate
    from .monitor import Monitor, Thresholds
    from .runner import Runner
    from .monitor import claude_judge
    runner = Runner(b, root)
    m = Monitor(Gate(runner), Thresholds(stall_s=a.stall_s, idle_s=a.idle_s, grace_s=a.grace_s),
                judge=claude_judge(runner.claude_bin) if a.llm_judge else None)
    try:
        while True:
            for f in m.step():
                print(f"{f.worker:<14} {f.state:<8} {f.action:<9} {f.detail}", flush=True)
            if not a.watch:
                m.join()
                return 0
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0


def cmd_limits(b: Board, a) -> int:
    from .limits import FIELDS, LimitsStore
    st = LimitsStore(b)
    if a.set:
        if os.environ.get("SWARM_ROLE"):  # the board CLI is open to every agent; budgets are the human's call
            print("error: only a human can change budgets", file=sys.stderr)
            return 3
        try:
            kv = dict(x.split("=", 1) for x in a.set)
            st.set(**kv)
        except (ValueError, TypeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    snap = st.snapshot()
    for k in FIELDS:
        use = {"max_workers": "workers", "max_wakeups": "wakeups", "max_wall_s": "wall_s", "max_tokens": "tokens"}[k]
        print(f"{k:<14} {snap['usage'][use]} / {snap['limits'][k]}")
    if a.set:
        print("paused work resumes within ~15s (the monitor picks it up)")
    return 0


def cmd_approvals(b: Board, a) -> int:
    from .approvals import ApprovalError, Approvals
    ap = Approvals(b)
    if a.cmd in ("approve", "deny"):
        if os.environ.get("SWARM_ROLE"):
            print("error: only a human can answer approval requests", file=sys.stderr)
            return 3
        decision = "deny" if a.cmd == "deny" else ("program" if a.program else "allow")
        try:
            x = ap.decide(a.id, decision, a.note or "")
        except ApprovalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        from .workers import Registry
        Registry(b).enqueue(Registry(b).get(x.worker), "human", Approvals.message(x))
        print(f"{x.id}: {x.status} ({x.decision}); {x.worker} is told within ~15s")
        return 0
    rows = ap.list(None if a.all else "pending")
    for x in rows:
        grant = f"  [--program grants {x.program}]" if x.program and x.status == "pending" else ""
        print(f"{x.id}  {x.status:<8} {x.worker:<12} {x.command}{grant}\n    denied: {x.reason[:160]}")
    if not rows:
        print("no pending approval requests" if not a.all else "no approval requests")
    return 0


def cmd_mention(b: Board, a, root: Path) -> int:
    from .runner import Runner
    role, sender = os.environ.get("SWARM_ROLE"), os.environ.get("SWARM_WORKER")
    if role == "critic":
        print("error: critics cannot mention workers; post `critique` findings", file=sys.stderr)
        return 3
    if role == "researcher":
        print("error: researchers cannot mention workers; post your findings as `research` events", file=sys.stderr)
        return 3
    if role == "verifier":
        print("error: verifiers cannot mention workers; give a verdict instead", file=sys.stderr)
        return 3
    if role == "worker" and sender:
        out = Runner(b, root).enqueue_mention(sender, a.target, a.message)
    else:  # a human at a terminal: same effect as the UI box, but queue-only (a running monitor/server wakes it)
        r = Runner(b, root)
        w = r.reg.get(a.target)
        b.post(EventIn(agent="human", kind="mention", body=f"@{a.target} {a.message}"[:8000]))
        r.reg.enqueue(w, "human", a.message)
        out = {"ok": True, "queued": a.target}
    if not out["ok"]:
        print(f"error: {out['error']}", file=sys.stderr)
        return 2
    print(f"queued for {out['queued']} (woken by the monitor within ~15s)")
    return 0


def cmd_integrate(b: Board, a, root: Path) -> int:
    from .gate import Gate
    from .integrate import IntegrationError, Integrator
    from .runner import Runner
    from .tasks import Tasks
    integ = Integrator(Gate(Runner(b, root)))
    try:
        if a.apply:
            out = integ.apply()
        else:
            for _ in range(10):  # resolve conflicts one worker at a time
                integ.last_thread = None
                out = integ.merge_ready(auto_resolve=not a.no_auto)
                if integ.last_thread is None:
                    break
                integ.last_thread.join()
            if a.verify and out.get("allMerged"):
                integ.verify_integration().join()
                out["verification"] = Tasks(b).get("integrator").status
    except IntegrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


def cmd_tasks(b: Board, a) -> int:
    from .tasks import Tasks
    for t in Tasks(b).list():
        print(f"{t.worker:<14} {t.status:<22} rounds={t.rounds} {t.id}")
    return 0


def cmd_worker(b: Board, a, root: Path) -> int:
    from .models import WorkerSpec
    from .runner import Runner, RunnerError
    from .workers import WorkerError
    r = Runner(b, root)
    from .gate import Gate
    gate = Gate(r)
    try:
        if a.wcmd == "define":
            w = r.define(WorkerSpec(name=a.name, goal=a.goal, owns=a.owns, tools=a.tool or [], model=a.model, checks=a.check or []))
            print(f"{w.id} {w.name} session={w.session_id}")
        elif a.wcmd == "list":
            for w in r.reg.list():
                print(f"{w.name:<14} {w.status:<8} turns={w.turns} tokens={tokens.line(tokens.worker_view(w))} queue={r.reg.queue_len(w)} maxgap={w.max_gap_s:.0f}s owns={','.join(w.owns)}")
        elif a.wcmd == "invoke":
            res = gate.invoke(a.name, a.message, sender=a.sender)
            print(json.dumps(res.model_dump(by_alias=True), indent=2) if a.json else res.text)
            return 0 if res.ok else 1
    except (WorkerError, RunnerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_serve(b: Board, a, root: Path) -> int:
    import uvicorn
    from .api import create_app
    tok_file = root / ".swarm" / "token"
    if not tok_file.exists():
        tok_file.write_text(secrets.token_urlsafe(24))
        tok_file.chmod(0o600)
    token = None if a.no_auth else tok_file.read_text().strip()
    from .gate import Gate
    from .mcp_server import SwarmTools
    from .runner import PROJECT_ROOT, Runner
    runner = Runner(b, root)
    tools = SwarmTools(b, runner, Gate(runner))  # no monitor thread here: the MCP server owns it
    url = f"http://127.0.0.1:{a.port}/" + (f"?token={token}" if token else "")
    print(f"swarmd on {url}\nauth={'token (.swarm/token)' if token else 'OFF'}", flush=True)
    uvicorn.run(create_app(b, token, tools, PROJECT_ROOT / "ui" / "dist"), host="127.0.0.1", port=a.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swarm", description="Swarm message board")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="start a new swarm (archives the previous one)"); s.add_argument("task")
    s = sub.add_parser("post", help="post an event")
    s.add_argument("agent"); s.add_argument("kind", choices=EVENT_KINDS)
    s.add_argument("message", help="text, or - to read stdin")
    s.add_argument("--task-id"); s.add_argument("--ref", action="append")
    s = sub.add_parser("show", help="print events"); s.add_argument("n", nargs="?", type=int, default=0)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("tail", help="follow the board"); s.add_argument("-n", type=int, default=10)
    s.add_argument("--once", action="store_true")
    s = sub.add_parser("digest", help="compact board summary for agents"); s.add_argument("--since", type=int, default=0)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("stale", help="workers with no recent activity"); s.add_argument("minutes", nargs="?", type=float, default=10)
    s.add_argument("--fail", action="store_true", help="exit 1 if any stale")
    s = sub.add_parser("export", help="render markdown"); s.add_argument("path", nargs="?")
    w = sub.add_parser("worker", help="define / list / @mention workers").add_subparsers(dest="wcmd", required=True)
    x = w.add_parser("define"); x.add_argument("name"); x.add_argument("--goal", required=True)
    x.add_argument("--owns", nargs="+", required=True); x.add_argument("--tool", action="append"); x.add_argument("--model", default="sonnet")
    x.add_argument("--check", action="append", help="command the verifier must run before passing (repeatable)")
    w.add_parser("list")  # workers only
    x = w.add_parser("invoke"); x.add_argument("name"); x.add_argument("message"); x.add_argument("--sender", default="coordinator")
    x.add_argument("--json", action="store_true")
    s = sub.add_parser("verdict", help="verifier only: pass/fail a worker's task")
    s.add_argument("worker"); s.add_argument("verdict", choices=["pass", "fail"]); s.add_argument("--reason", action="append")
    s.add_argument("--force", action="store_true", help="human override")
    s = sub.add_parser("critique", help="critic only: report a finding (blocker|major|nit) or `ok` on a worker or `plan`")
    s.add_argument("target"); s.add_argument("severity", choices=["blocker", "major", "nit", "ok"]); s.add_argument("reason")
    s.add_argument("--at", help="file:line (required for blocker/major on a worker)"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("eval", help="compare single agent vs swarm modes on fixed tasks (spends money: dry run unless --yes)")
    s.add_argument("--task", action="append"); s.add_argument("--mode", action="append"); s.add_argument("--repeat", type=int, default=1)
    s.add_argument("--max-cost", type=float, default=3.0, help="USD cap per run for the top-level agent"); s.add_argument("--model", default="sonnet")
    s.add_argument("--dir", help="eval directory (default: evals/ in the plugin)"); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("report", help="run report: plan vs actual, decisions, research, critiques, verdicts, monitor actions, tokens by role")
    s.add_argument("--out"); s.add_argument("--json", action="store_true")
    sub.add_parser("tasks", help="task statuses")
    s = sub.add_parser("integrate", help="merge done worker branches into the integration branch; --apply merges that into your branch")
    s.add_argument("--no-auto", action="store_true", help="report conflicts but do not start the integrator")
    s.add_argument("--apply", action="store_true")
    s.add_argument("--verify", action="store_true", help="after merging everything, run a read-only verifier over the merged branch")
    s = sub.add_parser("mention", help="@mention a worker (queued; the monitor wakes it)")
    s.add_argument("target"); s.add_argument("message")
    s = sub.add_parser("approvals", help="list approval requests for denied commands (--all for answered ones too)")
    s.add_argument("--all", action="store_true")
    s = sub.add_parser("approve", help="human: allow a denied command (--program grants the whole program)")
    s.add_argument("id"); s.add_argument("--program", action="store_true"); s.add_argument("--note")
    s = sub.add_parser("deny", help="human: refuse a denied command, with an optional note for the worker")
    s.add_argument("id"); s.add_argument("--note")
    s = sub.add_parser("limits", help="show swarm budget usage; humans raise limits with --set k=v ...")
    s.add_argument("--set", nargs="+", metavar="K=V")
    s = sub.add_parser("monitor", help="diagnose workers and climb the escalation ladder (--watch to loop)")
    s.add_argument("--watch", action="store_true"); s.add_argument("--interval", type=float, default=15)
    s.add_argument("--stall-s", type=float, default=300); s.add_argument("--idle-s", type=float, default=120)
    s.add_argument("--grace-s", type=float, default=60)
    s.add_argument("--llm-judge", action="store_true", help="ask a small model whether a quiet worker is progressing before killing it")
    s = sub.add_parser("serve", help="run the HTTP API + SSE on 127.0.0.1")
    s.add_argument("--port", type=int, default=7777); s.add_argument("--no-auth", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    root = find_root()
    ensure_swarm_dir(root)
    b = open_board(root)
    try:
        if a.cmd == "serve":
            return cmd_serve(b, a, root)
        if a.cmd == "report":
            return cmd_report(b, a, root)
        if a.cmd == "eval":
            return cmd_eval(a, root)
        if a.cmd == "verdict":
            return cmd_verdict(b, a)
        if a.cmd == "critique":
            return cmd_critique(b, a)
        if a.cmd == "monitor":
            return cmd_monitor(b, a, root)
        if a.cmd == "integrate":
            return cmd_integrate(b, a, root)
        if a.cmd == "mention":
            return cmd_mention(b, a, root)
        if a.cmd == "limits":
            return cmd_limits(b, a)
        if a.cmd in ("approvals", "approve", "deny"):
            return cmd_approvals(b, a)
        if a.cmd == "tasks":
            return cmd_tasks(b, a)
        if a.cmd == "worker":
            return cmd_worker(b, a, root)
        return {"init": cmd_init, "post": cmd_post, "show": cmd_show, "tail": cmd_tail, "digest": cmd_digest,
                "stale": cmd_stale, "export": cmd_export}[a.cmd](b, a)
    except NoActiveSwarm as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:  # pydantic validation
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
