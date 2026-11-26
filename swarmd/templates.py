"""Starter project templates: a known, runnable scaffold for a NEW product built with /todo.

Layout: templates/<name>/template.json + templates/<name>/files/... A template may `include` other templates into a
subdirectory (fullstack = react-app in frontend/ + fastapi-api in backend/); its own files are laid over them.
Files named `_gitignore` are written as `.gitignore` (a real .gitignore inside templates/ would apply to gswan itself).

apply() never overwrites a file (an existing .gitignore only gets the template's missing lines appended) and refuses a
destination that already holds anything besides the files a fresh repo usually has (ALLOWED_EXISTING) unless force.
It writes MARKER (`.todo-template.json`: name, title, run, install, cwd) at the destination root; the result API reads it.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKER = ".todo-template.json"
ALLOWED_EXISTING = ("README*", "LICENSE*", ".gitignore", ".git", ".swarm", ".claude", ".mcp.json", ".DS_Store")
_SKIP = {"__pycache__", "node_modules", ".DS_Store", ".venv", "dist"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class TemplateError(ValueError):
    pass


def templates_dir() -> Path:
    """$SWARM_TEMPLATES_DIR, else templates/ next to the package (plugin / source checkout), else the wheel copy."""
    if os.environ.get("SWARM_TEMPLATES_DIR"):
        return Path(os.environ["SWARM_TEMPLATES_DIR"])
    for d in (PROJECT_ROOT / "templates", Path(__file__).resolve().parent / "_templates"):
        if d.is_dir():
            return d
    return PROJECT_ROOT / "templates"


def _load(name: str) -> dict:
    if not _NAME.match(name or ""):
        raise TemplateError(f"bad template name {name!r}")
    f = templates_dir() / name / "template.json"
    if not f.is_file():
        raise TemplateError(f"unknown template {name!r}; available: {', '.join(t['name'] for t in list_templates()) or 'none'}")
    t = json.loads(f.read_text())
    t["name"] = name
    t.setdefault("cwd", "")
    t.setdefault("tools", []); t.setdefault("checks", []); t.setdefault("stack", []); t.setdefault("include", [])
    return t


def get(name: str) -> dict:
    """The template's metadata: name, title, description, stack, install, run, cwd, tools, checks (and include)."""
    return _load(name)


def list_templates() -> list[dict]:
    d = templates_dir()
    out = []
    for p in sorted(d.iterdir()) if d.is_dir() else []:
        if (p / "template.json").is_file() and _NAME.match(p.name):
            t = _load(p.name)
            out.append({k: t.get(k) for k in ("name", "title", "description", "stack", "install", "run", "cwd", "tools", "checks")})
    return out


def files(name: str, _seen: tuple[str, ...] = ()) -> dict[str, Path]:
    """Relative destination path (posix) -> source file, with includes resolved."""
    if name in _seen:
        raise TemplateError(f"template include cycle: {' -> '.join((*_seen, name))}")
    t = _load(name)
    out: dict[str, Path] = {}
    for inc in t["include"]:
        into = inc.get("into", "").strip("/")
        if ".." in Path(into).parts or Path(into).is_absolute():
            raise TemplateError(f"bad include target {into!r}")
        for rel, src in files(inc["template"], (*_seen, name)).items():
            out[f"{into}/{rel}" if into else rel] = src
    base = templates_dir() / name / "files"
    for src in sorted(base.rglob("*")) if base.is_dir() else []:
        rel = src.relative_to(base)
        if not src.is_file() or any(part in _SKIP for part in rel.parts) or src.suffix == ".pyc":
            continue
        parts = [".gitignore" if p == "_gitignore" else p for p in rel.parts]
        out["/".join(parts)] = src
    return out


def blockers(dest: Path) -> list[str]:
    """Top-level entries of dest that make it not a new/empty project."""
    if not dest.exists():
        return []
    return sorted(p.name for p in dest.iterdir() if not any(fnmatch.fnmatch(p.name, pat) for pat in ALLOWED_EXISTING))


def marker(t: dict) -> dict:
    return {"name": t["name"], "title": t["title"], "run": t["run"], "install": t["install"], "cwd": t.get("cwd", "")}


def apply(name: str, dest: str | Path, force: bool = False) -> list[str]:
    """Copy template `name` into dest without overwriting anything. Returns the relative paths written or changed."""
    t = _load(name)
    dest = Path(dest)
    if not force and (bad := blockers(dest)):
        raise TemplateError(f"{dest} is not empty ({', '.join(bad[:8])}{' ...' if len(bad) > 8 else ''}); "
                            "templates only start new projects (use force to add the files that do not exist yet)")
    srcs = files(name)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for rel, src in srcs.items():
        target = dest / rel
        if target.is_symlink() or (target.exists() and not target.is_file()):
            continue
        if target.exists():
            if target.name == ".gitignore":  # merge: add the lines the template needs, keep the user's
                have = target.read_text().splitlines()
                add = [ln for ln in src.read_text().splitlines() if ln.strip() and ln not in have]
                if add:
                    body = target.read_text()
                    target.write_text(body + ("" if body.endswith("\n") or not body else "\n") + "\n".join(add) + "\n")
                    written.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
        target.chmod(src.stat().st_mode & 0o777)
        written.append(rel)
    m = dest / MARKER
    if not m.exists():
        m.write_text(json.dumps(marker(t), indent=2) + "\n")
        written.append(MARKER)
    return written


# ---- git ----------------------------------------------------------------------
def _git_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    if subprocess.run(["git", "-C", str(root), "config", "user.email"], capture_output=True, text=True).stdout.strip() == "":
        env.update(GIT_AUTHOR_NAME="swarm", GIT_AUTHOR_EMAIL="swarm@localhost",
                   GIT_COMMITTER_NAME="swarm", GIT_COMMITTER_EMAIL="swarm@localhost")
    return env


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=_git_env(root))


def is_repo_root(root: Path) -> bool:
    return (Path(root) / ".git").exists()


def has_commit(root: Path) -> bool:
    return _git(Path(root), "rev-parse", "--verify", "-q", "HEAD").returncode == 0


def ensure_repo(root: Path) -> None:
    if not is_repo_root(root):
        r = _git(Path(root), "init", "-q")
        if r.returncode != 0:
            raise TemplateError(f"git init failed: {r.stderr.strip()}")


def commit(root: Path, paths: list[str], message: str) -> str:
    """Commit exactly `paths` on the current branch; returns the new commit sha."""
    root = Path(root)
    if not paths:
        raise TemplateError("nothing to commit")
    for cmd in (["add", "--", *paths], ["commit", "-q", "-m", message, "--", *paths]):
        r = _git(root, *cmd)
        if r.returncode != 0:
            raise TemplateError(f"git {cmd[0]} failed: {(r.stderr or r.stdout).strip()}")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def commit_message(name: str) -> str:
    return f"Start from template {name}"


def new_project(directory: str | Path, name: str) -> list[str]:
    """mkdir + git init + apply + initial commit "Start from template <name>". Refuses a non-empty directory."""
    d = Path(directory)
    _load(name)
    if bad := blockers(d):
        raise TemplateError(f"{d} is not empty ({', '.join(bad[:8])}); pick a new directory")
    d.mkdir(parents=True, exist_ok=True)
    ensure_repo(d)
    written = apply(name, d)
    commit(d, written, commit_message(name))
    return written
