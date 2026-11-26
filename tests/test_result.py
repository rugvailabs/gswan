import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swarmd import result
from swarmd.api import create_app
from swarmd.board import Board
from swarmd.models import WorkerSpec
from test_integrate import setup
from test_report import commit, make
from test_runner import git, repo  # noqa: F401

H = {"Authorization": "Bearer tok"}


def client(board):
    return TestClient(create_app(board, "tok"))


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


# ---- location -----------------------------------------------------------------------------------------------------
def test_no_swarm_and_no_worktree_is_stage_none(repo):
    board = Board(repo / ".swarm" / "swarm.db")
    v = result.build(board, repo)
    assert v.stage == "none" and v.location is None and v.files == [] and v.summary.total == 0
    board.start_swarm("t")
    from swarmd.runner import Runner
    Runner(board, repo).define(WorkerSpec(name="api", goal="build api", owns=["src/api"]))
    v = result.build(board, repo)
    assert v.stage == "none" and v.location is None and v.workers[0].name == "api" and v.workers[0].path is None
    with pytest.raises(result.NoLocation, match="no worktree"):
        result.build(board, repo, "api")
    with pytest.raises(result.NoLocation, match="unknown worker"):
        result.build(board, repo, "ghost")


def test_single_worker_is_the_default_location_with_file_statuses(repo):
    board, r, w = make(repo)
    wt = Path(w.worktree)
    commit(w, ["src/api/a.py"])
    (wt / "README").write_text("changed\n")                  # modified, uncommitted
    (wt / "notes.txt").write_text("new")                     # untracked -> added
    (wt / "node_modules/x").mkdir(parents=True); (wt / "node_modules/x/i.js").write_text("1")
    v = result.build(board, repo)
    assert v.stage == "workers" and v.location.kind == "worker" and v.location.worker == "api"
    assert v.location.path == w.worktree and v.location.branch == w.branch and v.verified is None and v.apply_command is None
    st = {f.path: f.status for f in v.files}
    assert st == {"src/api/a.py": "added", "notes.txt": "added", "README": "modified"}
    assert [f.path for f in v.files] == ["README", "notes.txt", "src/api/a.py"]  # changed files sorted by path
    assert v.summary.model_dump() == {"added": 2, "modified": 1, "deleted": 0, "total": 3}
    git(wt, "rm", "-qf", "README")
    v = result.build(board, repo)
    assert {f.path: f.status for f in v.files}["README"] == "deleted" and v.summary.deleted == 1


def test_two_workers_without_integration_have_no_default(repo):
    board, r, w = make(repo)
    r.define(WorkerSpec(name="ui", goal="build ui", owns=["src/ui"])); r.invoke("ui", "go")
    v = result.build(board, repo)
    assert v.stage == "workers" and v.location is None and {x.name for x in v.workers} == {"api", "ui"}
    assert all(x.path for x in v.workers) and v.workers[0].task_status == "open"
    assert result.build(board, repo, "ui").location.worker == "ui"


def test_unchanged_files_listed_after_changes_and_capped(repo, monkeypatch):
    board, r, w = make(repo)
    commit(w, [f"src/api/f{i}.py" for i in range(5)])
    monkeypatch.setattr(result, "MAX_FILES", 3)
    v = result.build(board, repo)
    assert len(v.files) == 3 and all(f.status == "added" for f in v.files) and v.summary.total == 6
    monkeypatch.setattr(result, "MAX_FILES", 500)
    assert result.build(board, repo).files[-1].model_dump() == {"path": "README", "status": "unchanged"}


def test_integration_then_applied(repo):
    board, r, team, gate, integ = setup(repo, {"api": {"src/api/a.py": "A\n"}, "ui": {"src/ui/u.py": "U\n"}})
    for n in ("api", "ui"):
        gate.invoke(n, "go")
    assert integ.merge_ready()["allMerged"]
    v = result.build(board, repo)
    wt, branch = integ.paths()
    assert v.stage == "integration" and v.location.kind == "integration" and v.location.path == str(wt)
    assert v.location.branch == branch and v.merged == ["api", "ui"] and v.apply_command == "swarm integrate --apply"
    assert v.verified is None  # not verified yet
    assert {f.path: f.status for f in v.files} == {"src/api/a.py": "added", "src/ui/u.py": "added", "README": "unchanged"}
    assert [x.name for x in v.workers] == ["api", "ui"]  # integrator is not listed
    integ.verify_integration().join(10)
    assert result.build(board, repo).verified is True
    assert result.build(board, repo, "api").location.kind == "worker"
    assert integ.apply()["ok"]
    v = result.build(board, repo)
    assert v.stage == "applied" and v.location.kind == "repo" and v.location.path == str(repo) and v.location.branch is None
    assert v.apply_command is None and v.verified is True
    assert {f.path: f.status for f in v.files}["src/api/a.py"] == "added"


# ---- project detection --------------------------------------------------------------------------------------------
def write(root: Path, files: dict[str, str]) -> Path:
    for p, text in files.items():
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_text(text)
    return root


def test_detect_node_react_vite_and_package_managers(tmp_path):
    pkg = {"scripts": {"start": "node x", "dev": "vite"}, "dependencies": {"react": "19"}, "devDependencies": {"vite": "6"}}
    d = write(tmp_path / "a", {"package.json": json.dumps(pkg)})
    p = result.detect_project(d)
    assert (p.kind, p.label, p.run_command, p.install_command, p.cwd) == ("node", "React app (Vite)", ["npm", "run", "dev"], ["npm", "install"], "")
    d = write(tmp_path / "b", {"package.json": json.dumps({"scripts": {"start": "node s.js"}, "dependencies": {"express": "4"}}), "pnpm-lock.yaml": ""})
    p = result.detect_project(d)
    assert (p.label, p.run_command, p.install_command) == ("Node server (Express)", ["pnpm", "run", "start"], ["pnpm", "install"])
    d = write(tmp_path / "c", {"package.json": json.dumps({"scripts": {"dev": "next dev"}, "dependencies": {"next": "15"}}), "yarn.lock": ""})
    assert result.detect_project(d).run_command == ["yarn", "dev"]


def test_detect_python_fastapi_and_plain(tmp_path):
    d = write(tmp_path / "a", {"pyproject.toml": "[project]\nname='x'\n", "app/main.py": "from fastapi import FastAPI\napi = FastAPI()\n"})
    p = result.detect_project(d)
    assert (p.kind, p.label, p.run_command, p.install_command) == ("python", "Python API (FastAPI)",
                                                                   ["uv", "run", "uvicorn", "app.main:api", "--reload"], ["uv", "sync"])
    d = write(tmp_path / "b", {"pyproject.toml": "[project]\nname='x'\n", "src/pkg/server.py": "import fastapi\napp = fastapi.FastAPI(title='t')\n"})
    assert result.detect_project(d).run_command == ["uv", "run", "uvicorn", "pkg.server:app", "--reload", "--app-dir", "src"]
    d = write(tmp_path / "c", {"pyproject.toml": "[project]\nname='x'\n", "tool/__main__.py": "print(1)\n"})
    p = result.detect_project(d)
    assert (p.label, p.run_command) == ("Python app", ["uv", "run", "python", "-m", "tool"])
    d = write(tmp_path / "d", {"requirements.txt": "requests\n", "main.py": "print(1)\n"})
    p = result.detect_project(d)
    assert p.run_command == ["python3", "main.py"] and p.install_command == ["python3", "-m", "pip", "install", "-r", "requirements.txt"]
    d = write(tmp_path / "e", {"pyproject.toml": "[project]\nname='x'\n[project.scripts]\nhello='x:main'\n"})
    assert result.detect_project(d).run_command == ["uv", "run", "hello"]


def test_detect_fullstack_static_unknown_and_subfolder(tmp_path):
    files = {"frontend/package.json": json.dumps({"scripts": {"dev": "vite"}, "dependencies": {"react": "19", "vite": "6"}}),
             "backend/pyproject.toml": "[project]\nname='b'\n", "backend/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
    d = write(tmp_path / "fs", files)
    p = result.detect_project(d)
    assert p.kind == "fullstack" and p.label == "Full-stack app (React app (Vite) + Python API (FastAPI))" and p.cwd == ""
    assert p.run_command[:2] == ["sh", "-c"] and "(cd backend && uv run uvicorn main:app --reload)" in p.run_command[2]
    assert "(cd frontend && npm run dev)" in p.run_command[2] and "npm install" in p.install_command[2] and "uv sync" in p.install_command[2]
    nested = write(tmp_path / "outer", {f"myapp/{k}": v for k, v in files.items()})
    p = result.detect_project(nested)
    assert p.kind == "fullstack" and p.cwd == "myapp"
    p = result.detect_project(write(tmp_path / "st", {"index.html": "<h1>hi</h1>"}))
    assert (p.kind, p.label, p.run_command, p.install_command) == ("static", "Static website", ["python3", "-m", "http.server", "8000"], None)
    p = result.detect_project(write(tmp_path / "un", {"notes.txt": "x"}))
    assert p.kind == "unknown" and p.run_command is None


def test_detect_template_marker_wins(tmp_path):
    marker = {"name": "react-app", "title": "React app", "run": ["npm", "run", "dev"], "install": ["npm", "install"]}
    d = write(tmp_path / "t", {".todo-template.json": json.dumps(marker),
                               "package.json": json.dumps({"scripts": {"start": "x"}, "dependencies": {"react": "19"}})})
    p = result.detect_project(d)
    assert (p.kind, p.label, p.template, p.run_command, p.install_command) == ("node", "React app", "react-app", ["npm", "run", "dev"], ["npm", "install"])
    d = write(tmp_path / "u", {".todo-template.json": json.dumps({"name": "cli", "title": "Command-line tool", "run": ["python3", "cli.py"]})})
    p = result.detect_project(d)
    assert (p.kind, p.label, p.template, p.run_command, p.install_command) == ("unknown", "Command-line tool", "cli", ["python3", "cli.py"], None)


# ---- API ----------------------------------------------------------------------------------------------------------
FAKE_SERVER = ("import sys,time\nprint('booting', flush=True)\nprint('token=supersecretvalue123', flush=True)\n"
               "print('  Local:   \\x1b[1mhttp://localhost:5173/\\x1b[0m', flush=True)\ntime.sleep(60)\n")


def project_worker(repo, files):
    board, r, w = make(repo)
    write(Path(w.worktree), files)
    commit(w, [])
    return board, w


def test_endpoints_require_auth(repo):
    board, r, w = make(repo)
    c = client(board)
    for method, url in (("get", "/result"), ("get", "/result/run"), ("post", "/result/run/stop"), ("get", "/result/zip")):
        assert getattr(c, method)(url).status_code == 401
    assert c.post("/result/run", json={"confirm": True}).status_code == 401
    assert c.post("/result/open", json={"target": "folder"}).status_code == 401
    assert c.get("/result/zip?token=tok").status_code == 200  # ?token= works for plain download links


def test_get_result_is_camel_case(repo):
    board, w = project_worker(repo, {"package.json": json.dumps({"scripts": {"dev": "vite"}})})
    body = client(board).get("/result", headers=H).json()
    assert body["stage"] == "workers" and body["location"]["kind"] == "worker" and body["applyCommand"] is None
    assert body["project"]["runCommand"] == ["npm", "run", "dev"] and body["project"]["installCommand"] == ["npm", "install"]
    assert body["workers"][0]["taskStatus"] == "open" and body["summary"]["added"] == 1
    assert client(board).get("/result?worker=ghost", headers=H).status_code == 404


def test_run_start_log_url_stop_and_audit(repo):
    board, w = project_worker(repo, {"server.py": FAKE_SERVER,
                                     ".todo-template.json": json.dumps({"name": "t", "title": "T", "run": [sys.executable, "server.py"],
                                                                        "install": [sys.executable, "-c", "print('installed')"]})})
    with client(board) as c:
        assert c.get("/result/run", headers=H).json() == {"running": False, "command": None, "cwd": None, "startedAt": None,
                                                         "exitCode": None, "url": None, "log": []}
        assert c.post("/result/run", headers=H, json={}).status_code == 400
        assert c.post("/result/run", headers=H, json={"confirm": False}).status_code == 400
        s = c.post("/result/run", headers=H, json={"confirm": True, "install": True})
        assert s.status_code == 200 and s.json()["running"] and s.json()["command"] == [sys.executable, "server.py"]
        assert s.json()["cwd"] == w.worktree and s.json()["startedAt"]
        st = wait_for(lambda: (j := c.get("/result/run", headers=H).json())["url"] and j)
        assert st["url"] == "http://localhost:5173/" and st["running"] and "installed" in st["log"] and "booting" in st["log"]
        assert not any("supersecretvalue123" in line for line in st["log"])
        assert c.post("/result/run", headers=H, json={"confirm": True}).status_code == 409
        stopped = c.post("/result/run/stop", headers=H).json()
        assert stopped["running"] is False and stopped["exitCode"] is not None and stopped["url"] == "http://localhost:5173/"
    audit = [e.body for e in board.list_events(kind="human").items]
    assert any(b.startswith("Ran the project: ") and "server.py" in b and "installed" in b for b in audit)


def test_run_without_a_command_is_400_and_process_exit_is_reported(repo):
    board, w = project_worker(repo, {"notes.txt": "x"})
    c = client(board)
    r = c.post("/result/run", headers=H, json={"confirm": True})
    assert r.status_code == 400 and "don't know how to run" in r.json()["detail"]
    write(Path(w.worktree), {".todo-template.json": json.dumps({"name": "t", "title": "T", "run": [sys.executable, "-c", "print('bye')"]})})
    assert c.post("/result/run", headers=H, json={"confirm": True, "install": True}).status_code == 400  # no install step
    assert c.post("/result/run", headers=H, json={"confirm": True}).status_code == 200
    st = wait_for(lambda: (j := c.get("/result/run", headers=H).json())["exitCode"] is not None and j)
    assert st["exitCode"] == 0 and not st["running"] and "bye" in st["log"]


def test_shutdown_stops_the_running_project(repo):
    board, w = project_worker(repo, {"server.py": FAKE_SERVER,
                                     ".todo-template.json": json.dumps({"name": "t", "title": "T", "run": [sys.executable, "server.py"]})})
    app = create_app(board, "tok")
    with TestClient(app) as c:
        c.post("/result/run", headers=H, json={"confirm": True})
        wait_for(lambda: app.state.runs.state().url)
    assert not app.state.runs.running()


def test_open_uses_the_injected_opener(repo, monkeypatch):
    board, r, w = make(repo)
    calls = []
    monkeypatch.setattr(result, "launch", calls.append)
    monkeypatch.delenv("SWARM_OPEN_BIN", raising=False)
    monkeypatch.setattr(result.sys, "platform", "darwin")
    c = client(board)
    assert c.post("/result/open", headers=H, json={"target": "folder"}).json() == {"ok": True, "path": w.worktree}
    monkeypatch.setattr(result, "which", lambda name: "/usr/bin/code" if name == "code" else None)
    c.post("/result/open", headers=H, json={"target": "vscode"})
    monkeypatch.setattr(result, "which", lambda name: None)
    c.post("/result/open", headers=H, json={"target": "vscode"})
    assert calls == [["open", w.worktree], ["code", w.worktree], ["open", "-a", "Visual Studio Code", w.worktree]]
    monkeypatch.setattr(result.sys, "platform", "linux")
    r400 = c.post("/result/open", headers=H, json={"target": "vscode"})
    assert r400.status_code == 400 and "VS Code" in r400.json()["detail"]
    assert c.post("/result/open", headers=H, json={"target": "folder"}).status_code == 400
    monkeypatch.setenv("SWARM_OPEN_BIN", "/bin/echo")
    c.post("/result/open", headers=H, json={"target": "folder"})
    assert calls[-1] == ["/bin/echo", w.worktree]
    assert c.post("/result/open", headers=H, json={"target": "printer"}).status_code == 422


def test_zip_has_project_files_but_not_dependencies(repo):
    board, w = project_worker(repo, {"src/api/a.py": "A", ".gitignore": "dist/\n"})
    wt = Path(w.worktree)
    write(wt, {"node_modules/lib/index.js": "x", "dist/bundle.js": "x", "new.txt": "untracked", ".venv/bin/python": "x"})
    (wt / "link").symlink_to("/etc/hosts")
    r = client(board).get("/result/zip", headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="proj-result.zip"'
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert names == {"proj/README", "proj/src/api/a.py", "proj/.gitignore", "proj/new.txt"}


def test_actions_without_a_location_are_404(repo):
    board, r, w = make(repo)
    r.define(WorkerSpec(name="ui", goal="build ui", owns=["src/ui"])); r.invoke("ui", "go")
    c = client(board)
    assert c.get("/result/zip", headers=H).status_code == 404
    assert c.post("/result/open", headers=H, json={"target": "folder"}).status_code == 404
    assert c.post("/result/run", headers=H, json={"confirm": True}).status_code == 404
    assert c.get("/result/zip?worker=ui", headers=H).status_code == 200
