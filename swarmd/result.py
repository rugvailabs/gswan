"""Result API backend: WHERE the finished project is (integration worktree, a worker's worktree or the user's repo once
applied), what changed in it, what kind of project it is and how to run it, plus run/stop, open-in-app and zip.

Running executes agent-written code, so callers must get an explicit confirmation from the human first (the API
requires `confirm: true`) and an audit event is posted to the board."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
import zipfile
from collections import deque
from pathlib import Path
from typing import IO, Callable, Literal

from .board import Board
from .diffs import _git
from .integrate import integration_paths
from .models import CamelModel, EventIn, utc_now
from .redact import redact
from .tasks import TaskError, Tasks
from .workers import Registry, WorkerError

MAX_FILES = 500
LOG_LINES = 200
SKIP_DIRS = frozenset({"node_modules", ".git", ".swarm", ".venv"})
MARKER = ".todo-template.json"
URL_RE = re.compile(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?[^\s]*")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


class ResultError(Exception):
    """A request the location/project cannot satisfy (maps to 400)."""


class NoLocation(ResultError):
    """There is no result to act on yet, or the named worker has no worktree (maps to 404)."""


class RunBusy(ResultError):
    """A project process is already running (maps to 409)."""


# ---- response models ----------------------------------------------------------------------------------------------
class Location(CamelModel):
    path: str
    branch: str | None
    kind: Literal["integration", "worker", "repo"]
    worker: str | None = None


class WorkerRef(CamelModel):
    name: str
    branch: str | None
    path: str | None
    task_status: str | None


class Project(CamelModel):
    kind: Literal["node", "python", "fullstack", "static", "unknown"]
    label: str
    run_command: list[str] | None = None
    install_command: list[str] | None = None
    template: str | None = None
    cwd: str = ""


class FileEntry(CamelModel):
    path: str
    status: Literal["added", "modified", "deleted", "unchanged"]


class Summary(CamelModel):
    added: int = 0
    modified: int = 0
    deleted: int = 0
    total: int = 0


class ResultView(CamelModel):
    stage: Literal["none", "workers", "integration", "applied"]
    location: Location | None
    workers: list[WorkerRef]
    merged: list[str]
    verified: bool | None
    apply_command: str | None
    project: Project | None
    files: list[FileEntry]
    summary: Summary


class RunState(CamelModel):
    running: bool
    command: list[str] | None
    cwd: str | None
    started_at: str | None
    exit_code: int | None
    url: str | None
    log: list[str]


# ---- location -----------------------------------------------------------------------------------------------------
def _ok(cwd: Path | str, *args: str) -> bool:
    return _git(cwd, *args).returncode == 0


def _sha(cwd: Path | str, rev: str) -> str | None:
    r = _git(cwd, "rev-parse", "-q", "--verify", f"{rev}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 else None


def _verdict(status: str | None) -> bool | None:
    return True if status == "done" else False if status in ("failed", "escalated") else None


def _task_status(tasks: Tasks, name: str) -> str | None:
    try:
        return tasks.get(name).status
    except TaskError:
        return None


def _locate(board: Board, root: Path, worker: str | None) -> tuple[ResultView, str | None]:
    """The view without project/files, plus the base commit the location is compared against."""
    empty = ResultView(stage="none", location=None, workers=[], merged=[], verified=None, apply_command=None,
                       project=None, files=[], summary=Summary())
    if board.current_swarm() is None:
        if worker:
            raise NoLocation("no active swarm")
        return empty, None
    reg, tasks = Registry(board), Tasks(board)
    ws = [w for w in reg.list() if w.name != "integrator"]
    refs = [WorkerRef(name=w.name, branch=w.branch, path=w.worktree if w.worktree and Path(w.worktree).is_dir() else None,
                      task_status=_task_status(tasks, w.name)) for w in ws]
    base = next((w.base_sha for w in ws if w.base_sha), None)
    wt, ibranch = integration_paths(board, root)
    itip = _sha(root, ibranch)
    merged = [w.name for w in ws if itip and w.branch and _sha(root, w.branch)
              and (_sha(root, w.branch) != w.base_sha or _task_status(tasks, w.name) == "done")
              and _ok(root, "merge-base", "--is-ancestor", w.branch, ibranch)]
    applied = bool(itip and merged and itip != base and _ok(root, "merge-base", "--is-ancestor", ibranch, "HEAD"))
    stage = "applied" if applied else "integration" if wt.is_dir() else "workers" if any(r.path for r in refs) else "none"
    view = empty.model_copy(update={"stage": stage, "workers": refs, "merged": merged})
    integ_verdict = _verdict(_task_status(tasks, "integrator"))

    if worker:
        w = next((x for x in ws if x.name == worker), None)
        if w is None:
            try:
                reg.get(worker)
            except WorkerError:
                raise NoLocation(f"unknown worker '{worker}'")
            raise NoLocation(f"'{worker}' is not a worker with its own worktree")
        if not (w.worktree and Path(w.worktree).is_dir()):
            raise NoLocation(f"worker '{worker}' has no worktree yet (it has not run)")
        view.location = Location(path=w.worktree, branch=w.branch, kind="worker", worker=w.name)
        view.verified = _verdict(_task_status(tasks, w.name))
        return view, w.base_sha
    if stage == "applied":
        view.location = Location(path=str(root), branch=None, kind="repo")
        view.verified = integ_verdict
    elif stage == "integration":
        view.location = Location(path=str(wt), branch=ibranch, kind="integration")
        view.verified, view.apply_command = integ_verdict, "swarm integrate --apply"
    else:
        with_wt = [x for x in ws if x.worktree and Path(x.worktree).is_dir()]
        if len(with_wt) == 1:
            w = with_wt[0]
            view.location = Location(path=w.worktree, branch=w.branch, kind="worker", worker=w.name)
            view.verified = _verdict(_task_status(tasks, w.name))
            return view, w.base_sha
    return view, base


def resolve(board: Board, root: Path, worker: str | None = None) -> Location:
    """The location an action works on. Raises NoLocation when there is none yet."""
    view, _ = _locate(board, root, worker)
    if view.location is None:
        raise NoLocation("no result to show yet: no integration branch and not exactly one worker worktree"
                         if view.stage == "workers" else "nothing has been built yet")
    return view.location


# ---- files --------------------------------------------------------------------------------------------------------
def _skipped(path: str) -> bool:
    return any(part in SKIP_DIRS for part in path.split("/"))


def project_files(path: Path | str) -> list[str]:
    """Tracked + untracked-but-not-ignored files that exist in the location, minus dependency/tool folders."""
    r = _git(path, "ls-files", "-co", "--exclude-standard", "-z")
    if r.returncode != 0:
        raise ResultError(f"cannot list files in {path}: {r.stderr.strip()[:200]}")
    return sorted({p for p in r.stdout.split("\0") if p and not _skipped(p) and (Path(path) / p).is_file()})


def file_statuses(path: Path | str, base: str | None) -> tuple[list[FileEntry], Summary]:
    """Every file of the location vs `base` (working tree, so uncommitted edits count). Changed first, capped."""
    present = project_files(path)
    changed: dict[str, str] = {}
    if base:
        ns = _git(path, "diff", "--no-renames", "--name-status", "-z", base, "--")
        tok = [x for x in ns.stdout.split("\0") if x]
        for code, p in zip(tok[0::2], tok[1::2]):
            if not _skipped(p):
                changed[p] = {"A": "added", "D": "deleted"}.get(code[:1], "modified")
        tracked_at_base = {x for x in _git(path, "ls-tree", "-r", "--name-only", "-z", base).stdout.split("\0") if x}
        for p in present:  # untracked files are not in `git diff`
            if p not in tracked_at_base and p not in changed:
                changed[p] = "added"
    entries = [FileEntry(path=p, status=changed.get(p, "unchanged")) for p in present]
    entries += [FileEntry(path=p, status="deleted") for p, s in changed.items() if s == "deleted" and p not in set(present)]
    order = {"added": 0, "modified": 0, "deleted": 0, "unchanged": 1}
    entries.sort(key=lambda e: (order[e.status], e.path))
    summary = Summary(added=sum(e.status == "added" for e in entries), modified=sum(e.status == "modified" for e in entries),
                      deleted=sum(e.status == "deleted" for e in entries), total=len(entries))
    return entries[:MAX_FILES], summary


# ---- project detection --------------------------------------------------------------------------------------------
def _read_json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _cmd(v) -> list[str] | None:
    return [str(x) for x in v] if isinstance(v, list) and v and all(isinstance(x, str) for x in v) else None


def _node(d: Path) -> Project | None:
    pkg = _read_json(d / "package.json")
    if pkg is None:
        return None
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    pm = "pnpm" if (d / "pnpm-lock.yaml").exists() else "yarn" if (d / "yarn.lock").exists() else "npm"
    script = next((s for s in ("dev", "start") if s in scripts), None)
    run = None if script is None else ["yarn", script] if pm == "yarn" else [pm, "run", script]
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    vite = " (Vite)" if "vite" in deps else ""
    label = ("Next.js app" if "next" in deps else f"React app{vite}" if "react" in deps else f"Vue app{vite}" if "vue" in deps
             else f"Svelte app{vite}" if "svelte" in deps else "Web app (Vite)" if vite else "Node server (Express)" if "express" in deps
             else "Node.js app")
    return Project(kind="node", label=label, run_command=run, install_command=[pm, "install"])


def _py_files(d: Path, limit: int = 200) -> list[Path]:
    out: list[Path] = []
    for p in sorted(d.glob("*.py")) + sorted(d.glob("*/*.py")) + sorted(d.glob("*/*/*.py")):
        rel = p.relative_to(d).parts
        if not any(x in SKIP_DIRS or x.startswith(".") or x in ("tests", "test") for x in rel[:-1]) and not rel[-1].startswith("test_"):
            out.append(p)
        if len(out) >= limit:
            break
    return out


FASTAPI_APP = re.compile(r"^(\w+)\s*(?::[^=]+)?=\s*(?:fastapi\.)?FastAPI\(", re.M)


def _python(d: Path) -> Project | None:
    pyproject, reqs = d / "pyproject.toml", d / "requirements.txt"
    if not pyproject.exists() and not reqs.exists():
        return None
    uv = pyproject.exists()
    prefix = ["uv", "run"] if uv else ["python3", "-m"]
    install = ["uv", "sync"] if uv else ["python3", "-m", "pip", "install", "-r", "requirements.txt"]
    for f in _py_files(d):
        try:
            m = FASTAPI_APP.search(f.read_text(errors="replace"))
        except OSError:
            continue
        if m:
            rel = f.relative_to(d).with_suffix("").parts
            extra = []
            if rel[0] == "src" and len(rel) > 1:
                rel, extra = rel[1:], ["--app-dir", "src"]
            run = [*prefix, "uvicorn", f"{'.'.join(rel)}:{m.group(1)}", "--reload", *extra]
            return Project(kind="python", label="Python API (FastAPI)", run_command=run, install_command=install)
    run = None
    scripts = {}
    if uv:
        try:
            scripts = tomllib.loads(pyproject.read_text()).get("project", {}).get("scripts", {}) or {}
        except (OSError, tomllib.TOMLDecodeError):
            scripts = {}
    py = ["uv", "run", "python"] if uv else ["python3"]
    if scripts:
        run = ["uv", "run", sorted(scripts)[0]]
    else:
        pkgs = sorted(p.parent for p in [*d.glob("*/__main__.py"), *d.glob("src/*/__main__.py")]
                      if not any(x in SKIP_DIRS for x in p.parts))
        if pkgs:
            run = [*py, "-m", pkgs[0].name]
        else:
            main = next((n for n in ("main.py", "app.py") if (d / n).is_file()), None)
            run = [*py, main] if main else None
    return Project(kind="python", label="Python app", run_command=run, install_command=install)


def _static(d: Path) -> Project | None:
    if (d / "index.html").is_file():
        return Project(kind="static", label="Static website", run_command=["python3", "-m", "http.server", "8000"])
    return None


def _in(sub: str, cmd: list[str] | None) -> str | None:
    return None if cmd is None else f"(cd {shlex.quote(sub)} && {shlex.join(cmd)})"


def _fullstack(d: Path) -> Project | None:
    if not ((d / "frontend").is_dir() and (d / "backend").is_dir()):
        return None
    fe, be = _single(d / "frontend"), _single(d / "backend")
    parts = [p for p in (_in("backend", be.run_command if be else None), _in("frontend", fe.run_command if fe else None)) if p]
    run = ["sh", "-c", " & ".join(parts) + " & wait"] if parts else None
    inst = [p for p in (_in("backend", be.install_command if be else None), _in("frontend", fe.install_command if fe else None)) if p]
    names = " + ".join(p.label for p in (fe, be) if p)
    return Project(kind="fullstack", label=f"Full-stack app ({names})" if names else "Full-stack app",
                   run_command=run, install_command=["sh", "-c", " && ".join(inst)] if inst else None)


def _single(d: Path) -> Project | None:
    return _node(d) or _python(d) or _static(d)


def _detect_dir(d: Path) -> Project | None:
    marker = _read_json(d / MARKER)
    found = _fullstack(d) or _single(d)
    if marker is not None:
        base = found or Project(kind="unknown", label="Project")
        return base.model_copy(update={
            "label": str(marker.get("title") or base.label),
            "run_command": _cmd(marker.get("run")) or base.run_command,
            "install_command": _cmd(marker.get("install")) if "install" in marker else base.install_command,
            "template": str(marker["name"]) if marker.get("name") else None})
    return found


def detect_project(path: Path | str) -> Project:
    """What kind of project lives at `path` (or in one sub-folder of it) and how to install/run it."""
    root = Path(path)
    found = _detect_dir(root)
    if found:
        return found
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".")):
        found = _detect_dir(sub)
        if found:
            return found.model_copy(update={"cwd": sub.name})
    return Project(kind="unknown", label="Unknown project")


# ---- the whole view -----------------------------------------------------------------------------------------------
def build(board: Board, root: Path | str, worker: str | None = None) -> ResultView:
    root = Path(root)
    view, base = _locate(board, root, worker)
    if view.location is not None:
        loc = Path(view.location.path)
        view.project = detect_project(loc)
        view.files, view.summary = file_statuses(loc, base)
    return view


# ---- open ---------------------------------------------------------------------------------------------------------
def _launch(cmd: list[str]) -> None:
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


launch: Callable[[list[str]], None] = _launch  # tests replace this so nothing opens
which: Callable[[str], str | None] = shutil.which


def open_command(target: str, path: str) -> list[str]:
    """The command that opens `path` in the file manager or VS Code. `SWARM_OPEN_BIN` overrides both."""
    override = os.environ.get("SWARM_OPEN_BIN")
    if override:
        return [override, path]
    if target == "folder":
        if sys.platform == "darwin":
            return ["open", path]
        if which("xdg-open"):
            return ["xdg-open", path]
        raise ResultError("cannot open folders on this system (no 'xdg-open' found)")
    if target == "vscode":
        if which("code"):
            return ["code", path]
        if sys.platform == "darwin":
            return ["open", "-a", "Visual Studio Code", path]
        raise ResultError("VS Code was not found: install it and its 'code' command (VS Code > Command Palette > "
                          "'Shell Command: Install code command in PATH')")
    raise ResultError(f"unknown target '{target}'")


def open_location(path: str, target: str) -> None:
    try:
        launch(open_command(target, path))
    except OSError as e:
        raise ResultError(f"could not open {target}: {e}") from e


# ---- zip ----------------------------------------------------------------------------------------------------------
def zip_location(path: Path | str, name: str) -> IO[bytes]:
    """A zip of the location's project files under `<name>/`, rewound and ready to stream. Symlinks are left out."""
    out = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in project_files(path):
            p = Path(path) / rel
            if not p.is_symlink():
                z.write(p, f"{name}/{rel}")
    out.seek(0)
    return out


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "project"


# ---- run ----------------------------------------------------------------------------------------------------------
class Runs:
    """At most one project process per server. Output is merged, redacted and kept as the last LOG_LINES lines."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.command: list[str] | None = None
        self.cwd: str | None = None
        self.started_at: str | None = None
        self.exit_code: int | None = None
        self.url: str | None = None
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self.reader: threading.Thread | None = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def state(self) -> RunState:
        with self.lock:
            running = self.running()
            if self.proc is not None and not running and self.exit_code is None and (self.reader is None or not self.reader.is_alive()):
                self.exit_code = self.proc.returncode
            return RunState(running=running, command=self.command, cwd=self.cwd, started_at=self.started_at,
                            exit_code=self.exit_code, url=self.url, log=list(self.log))

    def start(self, command: list[str], cwd: str, install: list[str] | None = None) -> RunState:
        with self.lock:
            if self.running():
                raise RunBusy("the project is already running; stop it first")
            argv = ["sh", "-c", f"{shlex.join(install)} && exec {shlex.join(command)}"] if install else command
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "0", "NO_COLOR": "1", "BROWSER": "none"}
            self.log.clear()
            self.url, self.exit_code, self.command, self.cwd, self.started_at = None, None, command, cwd, utc_now()
            for c in ([install] if install else []) + [command]:
                self.log.append(redact(f"$ {shlex.join(c)}"))
            try:
                proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:
                self.proc, self.exit_code = None, 127
                self.log.append(f"could not start: {e}")
                raise ResultError(f"could not start {command[0]}: {e}") from e
            self.proc = proc
            self.reader = threading.Thread(target=self._read, args=(proc,), name="result-run", daemon=True)
            self.reader.start()
        return self.state()

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = ANSI_RE.sub("", raw.decode("utf-8", "replace")).rstrip("\r\n")
            with self.lock:
                if proc is not self.proc:
                    continue
                self.log.append(redact(line))
                if self.url is None:
                    m = URL_RE.search(line)
                    if m:
                        self.url = m.group(0).rstrip(".,;)'\"").replace("://0.0.0.0", "://localhost", 1)
        code = proc.wait()
        with self.lock:
            if proc is self.proc:
                self.exit_code = code

    def stop(self) -> RunState:
        from .runner import kill_tree
        with self.lock:
            proc = self.proc
        if proc is not None and proc.poll() is None:
            kill_tree(proc.pid, grace=3.0)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self.reader is not None:
            self.reader.join(timeout=2)
        return self.state()


def start_run(board: Board, root: Path | str, runs: Runs, worker: str | None = None, install: bool = False) -> RunState:
    """Detect the project at the location and start it. The caller has already checked the human's confirmation."""
    loc = resolve(board, Path(root), worker)
    project = detect_project(loc.path)
    if not project.run_command:
        raise ResultError(f"don't know how to run this project ({project.label}); open it in VS Code instead")
    if install and not project.install_command:
        raise ResultError("this project has no install step")
    if runs.running():
        raise RunBusy("the project is already running; stop it first")
    cwd = str(Path(loc.path) / project.cwd) if project.cwd else loc.path
    state = runs.start(project.run_command, cwd, project.install_command if install else None)
    shown = " && ".join(shlex.join(c) for c in ([project.install_command] if install else []) + [project.run_command])
    board.post(EventIn(agent="human", kind="human", body=f"Ran the project: {shown} (in {cwd})"))
    return state
