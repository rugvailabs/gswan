import json
import subprocess

import pytest

from swarmd import bashpolicy, cli, templates
from swarmd.board import Board
from swarmd.mcp_server import SwarmTools, build_server
from swarmd.runner import Runner
from test_runner import FakeClaude, repo  # noqa: F401  (fixture)

NAMES = {"react-app", "fastapi-api", "fullstack"}


def git_out(root, *a):
    return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True).stdout


def test_list_and_get():
    ts = {t["name"]: t for t in templates.list_templates()}
    assert NAMES <= set(ts)
    for name in NAMES:
        t = templates.get(name)
        assert t["title"] and t["description"] and t["stack"] and t["install"] and t["run"] and t["checks"] and t["tools"]
        assert isinstance(t["run"], list) and isinstance(t["install"], list) and "cwd" in t
    with pytest.raises(templates.TemplateError):
        templates.get("nope")
    with pytest.raises(templates.TemplateError):
        templates.get("../templates")


def test_template_files_are_scaffolds_not_installs():
    for name in NAMES:
        fs = templates.files(name)
        assert "README.md" in fs
        assert not any(p.startswith("node_modules") or "/node_modules/" in p or p.endswith(("package-lock.json", "uv.lock")) for p in fs)
        assert not any(p.endswith("_gitignore") for p in fs)
    assert {"package.json", "src/store.ts", "src/App.test.tsx", ".gitignore"} <= set(templates.files("react-app"))
    assert {"pyproject.toml", "app/main.py", "tests/test_api.py"} <= set(templates.files("fastapi-api"))
    full = templates.files("fullstack")
    assert {"frontend/package.json", "backend/app/main.py", "README.md", "dev.py"} <= set(full)
    assert "/api" in full["frontend/vite.config.ts"].read_text()  # the overlay wins over react-app's config
    assert json.loads(templates.files("react-app")["tsconfig.json"].read_text())["compilerOptions"]["strict"] is True


@pytest.mark.parametrize("name", sorted(NAMES))
def test_checks_pass_the_verifier_bash_policy(name, tmp_path):
    t = templates.get(name)
    for c in t["checks"]:  # exactly what mcp_server.define_worker does before accepting a check
        bashpolicy.check(c, role="verifier", worktree=str(tmp_path), cwd=None, grants=t["tools"],
                         cli_python=None, branch_prefix=None, env={})
    for c in [" ".join(t["install"])] if name != "fullstack" else []:  # workers can install deps with the grants
        bashpolicy.check(c, role="worker", worktree=str(tmp_path), cwd=None, grants=t["tools"], cli_python=None, branch_prefix=None, env={})


def test_apply_copies_and_writes_marker(tmp_path):
    dest = tmp_path / "p"
    written = templates.apply("react-app", dest)
    assert "package.json" in written and templates.MARKER in written and ".gitignore" in written
    assert (dest / "src" / "App.tsx").is_file() and "node_modules" in (dest / ".gitignore").read_text()
    m = json.loads((dest / templates.MARKER).read_text())
    assert m == {"name": "react-app", "title": templates.get("react-app")["title"], "run": ["npm", "run", "dev"],
                 "install": ["npm", "install"], "cwd": ""}


def test_apply_refuses_non_empty_and_never_overwrites(tmp_path):
    dest = tmp_path / "p"; dest.mkdir()
    (dest / "README.md").write_text("mine")
    (dest / "LICENSE").write_text("MIT")
    (dest / ".gitignore").write_text("secret.txt\n")
    (dest / ".swarm").mkdir(); (dest / ".claude").mkdir()
    written = templates.apply("fastapi-api", dest)  # only allowed files present: ok
    assert (dest / "README.md").read_text() == "mine" and "README.md" not in written
    gi = (dest / ".gitignore").read_text().splitlines()
    assert gi[0] == "secret.txt" and ".venv/" in gi and ".gitignore" in written  # merged, user's line kept

    other = tmp_path / "q"; other.mkdir(); (other / "main.py").write_text("print(1)")
    with pytest.raises(templates.TemplateError, match="not empty"):
        templates.apply("fastapi-api", other)
    (other / "app").mkdir(); (other / "app" / "main.py").write_text("keep")
    written = templates.apply("fastapi-api", other, force=True)
    assert (other / "app" / "main.py").read_text() == "keep" and "app/main.py" not in written
    assert (other / "main.py").read_text() == "print(1)" and "app/db.py" in written


def test_new_project_makes_repo_with_one_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "nogit"))  # no user.email: fallback author
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    d = tmp_path / "new" / "app"
    written = templates.new_project(d, "fullstack")
    assert (d / "frontend" / "src" / "App.tsx").is_file() and (d / "backend" / "app" / "main.py").is_file()
    assert git_out(d, "rev-list", "--count", "HEAD").strip() == "1"
    assert git_out(d, "log", "-1", "--format=%s").strip() == "Start from template fullstack"
    assert git_out(d, "status", "--porcelain").strip() == ""
    assert set(git_out(d, "ls-files").split()) == set(written)
    with pytest.raises(templates.TemplateError):
        templates.new_project(d, "fullstack")  # not empty any more


def test_cli_list_apply_new_and_agent_refusal(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SWARM_ROLE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["template", "list"]) == 0
    assert "fastapi-api" in capsys.readouterr().out
    assert not (tmp_path / ".swarm").exists()  # template commands never create a board
    assert cli.main(["new", "a", "--template", "react-app"]) == 0
    assert git_out(tmp_path / "a", "log", "--format=%s").strip() == "Start from template react-app"
    assert cli.main(["template", "apply", "fastapi-api", "--dir", "b"]) == 0 and (tmp_path / "b" / "app" / "main.py").exists()
    assert cli.main(["template", "apply", "fastapi-api", "--dir", "a"]) == 2  # non-empty
    for role in ("worker", "verifier", "coordinator"):
        monkeypatch.setenv("SWARM_ROLE", role)
        assert cli.main(["template", "apply", "react-app", "--dir", "c"]) == 3
        assert cli.main(["new", "d", "--template", "react-app"]) == 3
        assert cli.main(["template", "list"]) == 0
    assert not (tmp_path / "c").exists() and not (tmp_path / "d").exists()


@pytest.fixture
def tools(repo):
    board = Board(repo / ".swarm" / "swarm.db")
    return SwarmTools(board, Runner(board, repo, executor=FakeClaude()))


def test_mcp_apply_template_on_empty_repo(tools, repo):
    assert {t["name"] for t in tools.list_templates()["templates"]} >= NAMES
    tools.swarm_start("build a thing")
    out = tools.apply_template("fastapi-api")
    assert out["ok"] and out["posted"] and out["checks"] == ["uv run pytest -q"] and "Bash(uv run pytest:*)" in out["tools"]
    assert git_out(repo, "log", "-1", "--format=%s").strip() == "Start from template fastapi-api"
    assert git_out(repo, "status", "--porcelain").strip() == ""
    ev = [e for e in tools.board.events_after(0) if e.kind == "plan"]
    assert ev and "template:fastapi-api" in ev[-1].refs and "Backend API" in ev[-1].body
    # the checks it returns are accepted by define_worker as-is
    assert tools.define_worker("api", "g", ["app"], tools=out["tools"], checks=out["checks"])["ok"]
    again = tools.apply_template("react-app")  # now the repo has code
    assert again["ok"] is False and "not new/empty" in again["error"]


def test_mcp_apply_template_refuses_existing_project(tools, repo):
    (repo / "src").mkdir(); (repo / "src" / "x.py").write_text("x = 1\n")
    head = git_out(repo, "rev-parse", "HEAD")
    out = tools.apply_template("react-app")
    assert out["ok"] is False and "src" in out["error"]
    assert git_out(repo, "rev-parse", "HEAD") == head and not (repo / "package.json").exists()


def test_mcp_apply_template_makes_first_commit(tmp_path):
    root = tmp_path / "fresh"; root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".swarm").mkdir()
    board = Board(root / ".swarm" / "swarm.db")
    t = SwarmTools(board, Runner(board, root, executor=FakeClaude()))
    out = t.apply_template("react-app")  # no swarm active: still works, nothing posted
    assert out["ok"] and out["posted"] is False
    assert git_out(root, "rev-list", "--count", "HEAD").strip() == "1"


def test_template_tools_are_coordinator_only(tools):
    import asyncio
    names = lambda role: {t.name for t in asyncio.run(build_server(tools, role).list_tools())}  # noqa: E731
    assert {"list_templates", "apply_template"} <= names("coordinator")
    assert not {"list_templates", "apply_template"} & names("worker")
